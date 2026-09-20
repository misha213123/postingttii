from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

from .config import settings


class TokenStore:
    def __init__(self) -> None:
        self.key_path = settings.data_dir / "token.key"
        self.data_path = settings.data_dir / "accounts.enc"
        self._fernet = Fernet(self._load_or_create_key())

    def _load_or_create_key(self) -> bytes:
        if self.key_path.exists():
            return self.key_path.read_bytes().strip()

        key = Fernet.generate_key()
        self.key_path.write_bytes(key)
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass
        return key

    def _read(self) -> dict[str, list[dict[str, Any]]]:
        if not self.data_path.exists():
            return {"youtube": [], "tiktok": [], "instagram": []}

        raw = self._fernet.decrypt(self.data_path.read_bytes())
        data = json.loads(raw.decode("utf-8"))
        for platform in ("youtube", "tiktok", "instagram"):
            data.setdefault(platform, [])
        return data

    def _write(self, data: dict[str, list[dict[str, Any]]]) -> None:
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.data_path.write_bytes(self._fernet.encrypt(payload))

    def list_accounts(self) -> dict[str, list[dict[str, Any]]]:
        data = self._read()
        safe: dict[str, list[dict[str, Any]]] = {}
        for platform, accounts in data.items():
            safe[platform] = [
                {
                    "slot": a.get("slot"),
                    "label": a.get("label", f"{platform} #{a.get('slot')}"),
                    "id": a.get("id", ""),
                    "connected": True,
                }
                for a in sorted(accounts, key=lambda x: int(x.get("slot", 0)))
            ]
        return safe

    def get(self, platform: str, slot: int) -> dict[str, Any] | None:
        data = self._read()
        for item in data.get(platform, []):
            if int(item.get("slot", 0)) == int(slot):
                return item
        return None

    def save(self, platform: str, slot: int, payload: dict[str, Any]) -> None:
        data = self._read()
        accounts = data.setdefault(platform, [])
        accounts[:] = [a for a in accounts if int(a.get("slot", 0)) != int(slot)]
        accounts.append({"slot": int(slot), **payload})
        self._write(data)

    def delete(self, platform: str, slot: int) -> None:
        data = self._read()
        accounts = data.setdefault(platform, [])
        accounts[:] = [a for a in accounts if int(a.get("slot", 0)) != int(slot)]
        self._write(data)


store = TokenStore()
