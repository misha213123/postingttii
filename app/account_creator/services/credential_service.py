from __future__ import annotations

import os
import secrets
import string

from cryptography.fernet import Fernet

from app.config import settings


def _load_key() -> bytes:
    path = settings.data_dir / "token.key"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path.read_bytes().strip()
    key = Fernet.generate_key()
    path.write_bytes(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


_FERNET = Fernet(_load_key())


def generate_password(length: int = 16) -> str:
    length = max(12, min(20, int(length)))
    alphabet = string.ascii_letters + string.digits + "!@#_-"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(ch.islower() for ch in value)
            and any(ch.isupper() for ch in value)
            and any(ch.isdigit() for ch in value)
            and any(ch in "!@#_-" for ch in value)
        ):
            return value


def encrypt_secret(value: str) -> bytes:
    return _FERNET.encrypt(value.encode("utf-8"))


def decrypt_secret(value: bytes | str) -> str:
    token = value.encode("utf-8") if isinstance(value, str) else value
    return _FERNET.decrypt(token).decode("utf-8")
