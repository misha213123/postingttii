"""Per-account proxy configuration for manual Camoufox Instagram profiles.

Credentials are encrypted at rest using an independent local Fernet key, and
never returned by GET endpoints or supplied in command-line arguments.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.account_creator import database
from app.config import settings

router = APIRouter(prefix="/api/account-manager", tags=["instagram-camoufox"])
KEY_PATH = settings.data_dir / "camoufox_proxy.key"
CONFIG_PATH = settings.data_dir / "camoufox_proxies.enc"


class ProxyInput(BaseModel):
    server: str = Field(default="", max_length=255)
    username: str = Field(default="", max_length=255)
    password: str = Field(default="", max_length=512)


def _cipher() -> Fernet:
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not KEY_PATH.exists():
        key = Fernet.generate_key()
        # Prevent overwriting a key from a concurrent request.
        try:
            fd = os.open(KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as out:
                out.write(key)
    return Fernet(KEY_PATH.read_bytes().strip())


def _load() -> dict[str, dict[str, str]]:
    if not CONFIG_PATH.exists():
        return {}
    # Fail closed if the cipher key has been lost or config corrupted.
    raw = json.loads(_cipher().decrypt(CONFIG_PATH.read_bytes()).decode("utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("Некорректный файл конфигурации прокси")
    return raw


def _save(config: dict[str, dict[str, str]]) -> None:
    encrypted = _cipher().encrypt(json.dumps(config).encode("utf-8"))
    temp = CONFIG_PATH.with_suffix(".tmp")
    temp.write_bytes(encrypted)
    os.replace(temp, CONFIG_PATH)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def _check_server(server: str) -> str:
    server = server.strip()
    if not server:
        return ""
    try:
        parsed = urlsplit(server)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Укажи прокси с корректным портом") from exc
    if (
        parsed.scheme not in {"http", "https", "socks5"}
        or not parsed.hostname
        or not port
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Формат прокси: http://host:port или socks5://host:port")
    return server


def _account(account_id: int):
    if account_id < 1 or not database.get_account(account_id):
        raise HTTPException(404, "Аккаунт не найден")


def get_config(account_id: int) -> dict[str, str]:
    """Internal worker launcher reads credentials; never log or return this value."""
    return _load().get(str(account_id), {})


def public_config(account_id: int) -> dict:
    config = get_config(account_id)
    return {
        "server": config.get("server", ""),
        "username": config.get("username", ""),
        "password_set": bool(config.get("password")),
        "enabled": bool(config.get("server")),
    }


@router.get("/accounts/{account_id}/camoufox/proxy")
def get_proxy(account_id: int):
    _account(account_id)
    return public_config(account_id)


@router.put("/accounts/{account_id}/camoufox/proxy")
def set_proxy(account_id: int, body: ProxyInput):
    _account(account_id)
    # Do not modify connection parameters while a browser is using them.
    from app.account_creator.services.instagram_service import is_running
    if is_running(account_id):
        raise HTTPException(409, "Сначала закрой окно Camoufox")
    try:
        server = _check_server(body.server)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    current = _load()
    if server:
        previous = current.get(str(account_id), {})
        current[str(account_id)] = {
            "server": server,
            "username": body.username.strip(),
            "password": body.password or previous.get("password", ""),
        }
    else:
        current.pop(str(account_id), None)
    _save(current)
    return public_config(account_id)


def launch_proxy(account_id: int) -> dict:
    config = get_config(account_id)
    if not config.get("server"):
        return {}
    result = {"server": config["server"]}
    if config.get("username"):
        result["username"] = config["username"]
    if config.get("password"):
        result["password"] = config["password"]
    return result
