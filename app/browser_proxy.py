"""Parsing and testing user-supplied HTTP(S)/SOCKS5 proxies.

Proxy credentials stay in the local encrypted browser-account registry and
are never reflected by public API fields or exception messages.
"""
from __future__ import annotations

import ipaddress
import re
import time
from urllib.parse import quote, unquote, urlsplit

import httpx

SCHEMES = {"http", "https", "socks5"}


def parse_proxy(value: str) -> dict[str, str] | None:
    """Accept normal proxy URLs and residential host:port:user:password.

    Supported:
      host:port
      host:port:user:password
      http://host:port:user:password  (common provider copy format)
      http://user:password@host:port
      user:password@host:port
      https://host:port
      socks5://host:port
    """
    raw = (value or "").strip().strip('"').strip("'").strip()
    if not raw:
        return None
    if len(raw) > 1200 or any(c.isspace() for c in raw):
        raise ValueError("Адрес прокси слишком длинный или содержит пробелы")

    if "://" in raw:
        scheme, address = raw.split("://", 1)
        scheme = scheme.lower()
    else:
        scheme, address = "http", raw

    if scheme not in SCHEMES:
        raise ValueError("Поддерживаются прокси HTTP, HTTPS и SOCKS5")
    if not address:
        raise ValueError("Укажи адрес прокси и порт")

    # Some residential providers supply "http://IP:PORT:LOGIN:PASSWORD"
    # rather than an RFC 3986 proxy URL. urllib.parse.urlsplit interprets
    # the extra colon as part of the port and raises ValueError.
    # Translate that user-friendly format to a proper authenticated URL.
    # Use the remainder as an opaque password, including any ':', '@',
    # '#', '?' or '%' characters, and percent-encode it before URL parsing.
    provider_parts = re.fullmatch(
        r"([^:/@\[\]\s]+):(\d{1,5}):([^:\s]+):(.+)", address
    )
    if provider_parts:
        host, port, username, password = provider_parts.groups()
        if not username or not password:
            raise ValueError("Укажи и логин, и пароль прокси")
        address = (
            f"{quote(username, safe='')}:{quote(password, safe='')}@"
            f"{host}:{port}"
        )
    parsed_url = f"{scheme}://{address}"

    try:
        parsed = urlsplit(parsed_url)
        hostname, port = parsed.hostname, parsed.port
        username = unquote(parsed.username or "")
        password = unquote(parsed.password or "")
    except (ValueError, UnicodeError):
        raise ValueError(
            "Не удалось прочитать прокси. Используй IP:порт:логин:пароль "
            "или http://логин:пароль@IP:порт"
        ) from None

    if not hostname or port is None:
        raise ValueError("Не указан адрес или числовой порт прокси")
    if not 1 <= port <= 65535:
        raise ValueError("Порт прокси должен быть от 1 до 65535")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("В адресе прокси не допускаются путь и параметры")
    if (username and not password) or (password and not username):
        raise ValueError("Укажи одновременно логин и пароль прокси")
    if parsed.scheme == "socks5" and username:
        raise ValueError(
            "SOCKS5 с логином и паролем не поддерживается Edge Playwright; "
            "используй HTTP-прокси"
        )

    host = f"[{hostname}]" if ":" in hostname else hostname
    result = {"server": f"{scheme}://{host}:{port}"}
    if username:
        result["username"] = username
        result["password"] = password
    return result

def masked_proxy(raw: str) -> str:
    if not raw:
        return "Без прокси"
    try:
        return parse_proxy(raw)["server"]
    except (ValueError, TypeError):
        return "Неверные настройки"


def httpx_proxy(spec: dict[str, str]) -> str:
    """Construct an auth URI only in memory for a one-shot connection test."""
    if "username" not in spec:
        return spec["server"]
    url = urlsplit(spec["server"])
    return (f"{url.scheme}://{quote(spec['username'], safe='')}:"
            f"{quote(spec['password'], safe='')}@{url.netloc}")


async def check_proxy(raw: str) -> dict:
    """Check the outgoing proxy connection without disclosing credentials.

    Do not rely on raw exception text: HTTPX sometimes embeds proxy URI
    userinfo in an error string. Return only curated diagnostic categories.
    """
    spec = parse_proxy(raw)
    if spec is None:
        raise ValueError("Введи адрес прокси")

    start = time.monotonic()
    endpoint = "https://api.ipify.org?format=json"

    def failure(code: str, message: str, *, http_status: int | None = None) -> dict:
        result = {
            "ok": False,
            "code": code,
            "message": message,
            "latency_ms": round((time.monotonic() - start) * 1000),
            "proxy": spec["server"],
        }
        if http_status is not None:
            result["http_status"] = http_status
        return result

    try:
        async with httpx.AsyncClient(
            proxy=httpx_proxy(spec),
            trust_env=False,
            timeout=httpx.Timeout(18.0, connect=9.0),
            follow_redirects=False,
        ) as client:
            response = await client.get(endpoint)
            status = int(getattr(response, "status_code", 200))
            if status == 407:
                return failure(
                    "auth_required",
                    "Прокси требует авторизацию (HTTP 407). Проверь логин и пароль.",
                    http_status=407,
                )
            if status in (401, 403):
                return failure(
                    "access_denied",
                    f"Доступ отклонён (HTTP {status}). Проверь "
                    "логин, разрешённые IP и настройки зоны у провайдера.",
                    http_status=status,
                )
            if status >= 400:
                return failure(
                    "http_error",
                    f"Проверочный сервис ответил HTTP {status}. "
                    "Прокси доступен, но запрос не завершился успешно.",
                    http_status=status,
                )
            try:
                ip = str(response.json().get("ip") or "")
                ipaddress.ip_address(ip)
            except (ValueError, KeyError, TypeError, AttributeError):
                return failure(
                    "bad_ip_response",
                    "Через прокси получен ответ, но внешний IP определить "
                    "не удалось. Возможно, ответ изменён прокси-сервером.",
                )
    except httpx.ProxyError as exc:
        # HTTPX raises ProxyError when HTTPS CONNECT is rejected, so HTTP
        # 407/403/502 never reach the response.status_code checks above.
        # Its diagnostic generally starts with the upstream response code
        # (e.g. "407 Proxy Authentication Required"). Extract ONLY that code;
        # raw exception text may contain the user's proxy credentials.
        match = re.match(r"^\s*(\d{3})(?:\s|$)", str(exc))
        proxy_status = int(match.group(1)) if match else None
        if proxy_status == 407:
            return failure(
                "auth_required",
                "Прокси отклонил авторизацию (HTTP 407). Проверь логин и "
                "пароль, выбранный режим авторизации и активность тарифа.",
                http_status=407,
            )
        if proxy_status in (401, 403):
            return failure(
                "access_denied",
                f"Прокси запретил подключение (HTTP {proxy_status}). "
                "Проверь разрешённый исходящий IP, доступ и настройки "
                "аккаунта у провайдера.",
                http_status=proxy_status,
            )
        if proxy_status == 429:
            return failure(
                "rate_limited",
                "Прокси ограничил частоту запросов (HTTP 429). "
                "Проверь ограничения тарифа у провайдера.",
                http_status=429,
            )
        if proxy_status in (500, 502, 503, 504):
            return failure(
                "upstream_failure",
                f"Прокси-шлюз вернул HTTP {proxy_status}. "
                "Это ошибка сервера прокси или его соединения с целью.",
                http_status=proxy_status,
            )
        return failure(
            "proxy_rejected",
            "Прокси отклонил HTTPS-соединение (CONNECT), но точный "
            "HTTP-код не определён. Проверь тип прокси и данные "
            "подключения у провайдера.",
        )
    except httpx.ConnectTimeout:
        return failure(
            "connect_timeout",
            "Тайм-аут подключения к прокси. Проверь IP, порт "
            "и доступность сервера у провайдера.",
        )
    except httpx.ReadTimeout:
        return failure(
            "read_timeout",
            "Прокси принял подключение, но не ответил вовремя. "
            "Попробуй позже или проверь качество соединения.",
        )
    except httpx.ConnectError:
        return failure(
            "connect_error",
            "Не удалось установить соединение. Проверь адрес, порт "
            "и соответствует ли протокол HTTP/HTTPS/SOCKS5 настройкам провайдера.",
        )
    except httpx.TimeoutException:
        return failure(
            "timeout",
            "Истекло время ожидания ответа через прокси.",
        )
    except httpx.HTTPError:
        return failure(
            "network_error",
            "Сетевая ошибка при проверке через прокси. "
            "Проверь его протокол, авторизацию и доступность.",
        )
    except (ValueError, KeyError, TypeError, OSError):
        return failure(
            "test_error",
            "Не удалось завершить тест прокси. Проверь настройки подключения.",
        )

    return {
        "ok": True,
        "code": "ok",
        "ip": ip,
        "latency_ms": round((time.monotonic() - start) * 1000),
        "proxy": spec["server"],
    }
