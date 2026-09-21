from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet

from app.config import settings


class EncryptionService:
    def __init__(self) -> None:
        self.key_path = settings.data_dir / "token.key"
        self._fernet = Fernet(self._load_or_create_key())

    def _load_or_create_key(self) -> bytes:
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        if self.key_path.exists():
            return self.key_path.read_bytes().strip()

        key = Fernet.generate_key()
        self.key_path.write_bytes(key)
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass
        return key

    def encrypt_text(self, value: str) -> bytes:
        return self._fernet.encrypt(value.encode("utf-8"))

    def decrypt_text(self, value: bytes | str) -> str:
        token = value.encode("utf-8") if isinstance(value, str) else value
        return self._fernet.decrypt(token).decode("utf-8")


encryption_service = EncryptionService()
