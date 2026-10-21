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
HOST_PORT = re.compile(r"^[^:/@\s]+:\d{1,5}$")


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
    try:
        spec = parse_proxy(raw)
        if spec is None:
            raise ValueError("Введи адрес прокси")
        url = httpx_proxy(spec)
    except ValueError as exc:
        raise ValueError(str(exc)) from None

    started = time.monotonic()
    try:
        async with httpx.AsyncClient(
            proxy=url,
            trust_env=False,
            timeout=httpx.Timeout(16.0, connect=8.0),
            follow_redirects=False,
        ) as client:
            response = await client.get("https://api.ipify.org?format=json")
            response.raise_for_status()
            ip = str(response.json().get("ip") or "")
            ipaddress.ip_address(ip)
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        # Do not return raw httpx errors: they may echo user:password@proxy.
        return {
            "ok": False,
            "message": "Нет ответа через прокси. Проверь адрес, порт, тип и логин/пароль.",
        }
    return {
        "ok": True,
        "ip": ip,
        "latency_ms": round((time.monotonic() - started) * 1000),
        "proxy": spec["server"],
    }
