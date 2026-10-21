"""Parsing and testing user-supplied HTTP(S)/SOCKS5 proxies.

Proxy credentials stay in the local encrypted browser-account registry and
are never reflected by public API fields or exception messages.
"""
from __future__ import annotations

import ipaddress
import time
from urllib.parse import quote, unquote, urlsplit

import httpx

SCHEMES = {"http", "https", "socks5"}


def parse_proxy(value: str) -> dict[str, str] | None:
    raw = (value or "").strip().strip('"').strip("'")
    if not raw:
        return None
    if len(raw) > 1200 or any(c.isspace() for c in raw):
        raise ValueError("Адрес прокси слишком длинный или содержит пробелы")

    if "://" not in raw:
        # Common residential-provider format: host:port:username:password.
        # Allow colons in the password, but not in the hostname.
        if "@" not in raw and raw.count(":") >= 3 and not raw.startswith("["):
            host, port, username, password = raw.split(":", 3)
            if not host or not port or not username or not password:
                raise ValueError("Нужен формат host:port:user:password")
            raw = (
                "http://" + quote(username, safe="") + ":"
                + quote(password, safe="") + "@" + host + ":" + port
            )
        else:
            raw = "http://" + raw

    try:
        parsed = urlsplit(raw)
        port = parsed.port
        hostname = parsed.hostname
        username = unquote(parsed.username or "")
        password = unquote(parsed.password or "")
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Некорректный порт, адрес или авторизация прокси") from exc

    if parsed.scheme not in SCHEMES or not hostname or port is None:
        raise ValueError("Укажи HTTP, HTTPS или SOCKS5 прокси с адресом и портом")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("В адресе прокси не допускаются путь и параметры")
    if not 1 <= port <= 65535:
        raise ValueError("Порт прокси должен быть от 1 до 65535")
    if (username and not password) or (password and not username):
        raise ValueError("Укажи одновременно логин и пароль прокси")
    if parsed.scheme == "socks5" and username:
        raise ValueError("SOCKS5 с логином и паролем не поддерживается Edge Playwright; используй HTTP-прокси")

    host = f"[{hostname}]" if ":" in hostname else hostname
    result = {"server": f"{parsed.scheme}://{host}:{port}"}
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
            if response.status_code == 407:
                return failure(
                    "auth_required",
                    "Прокси требует авторизацию (HTTP 407). Проверь логин и пароль.",
                    http_status=407,
                )
            if response.status_code in (401, 403):
                return failure(
                    "access_denied",
                    f"Доступ отклонён (HTTP {response.status_code}). Проверь "
                    "логин, разрешённые IP и настройки зоны у провайдера.",
                    http_status=response.status_code,
                )
            if response.status_code >= 400:
                return failure(
                    "http_error",
                    f"Проверочный сервис ответил HTTP {response.status_code}. "
                    "Прокси доступен, но запрос не завершился успешно.",
                    http_status=response.status_code,
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
    except httpx.ProxyError:
        return failure(
            "proxy_rejected",
            "Прокси отклонил соединение. Проверь логин/пароль, "
            "протокол и разрешённые IP в кабинете провайдера.",
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
