from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from app.config import settings


class PublishStateStore:
    def __init__(self) -> None:
        self.path = settings.data_dir / "publish_state.json"

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    @staticmethod
    def video_key(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def completed_targets(self, video_key: str) -> dict[str, Any]:
        data = self._read()
        return data.get(video_key, {}).get("targets", {})

    def is_completed(self, video_key: str, target: str) -> bool:
        return target in self.completed_targets(video_key)

    def mark_completed(
        self,
        video_key: str,
        filename: str,
        target: str,
        result: dict[str, Any] | None = None,
    ) -> None:
        data = self._read()
        item = data.setdefault(
            video_key,
            {
                "filename": filename,
                "created_at": int(time.time()),
                "targets": {},
            },
        )
        item["filename"] = filename
        item.setdefault("targets", {})[target] = {
            "completed_at": int(time.time()),
            "result": result or {},
        }
        self._write(data)

    def last_completed_at(self, target: str) -> int:
        latest = 0
        for item in self._read().values():
            target_info = item.get("targets", {}).get(target)
            if target_info:
                latest = max(latest, int(target_info.get("completed_at", 0)))
        return latest

    def cooldown_remaining(self, target: str, cooldown_seconds: int) -> int:
        if cooldown_seconds <= 0:
            return 0
        last = self.last_completed_at(target)
        if not last:
            return 0
        return max(0, cooldown_seconds - (int(time.time()) - last))

    def summary(self, video_key: str) -> list[str]:
        return sorted(self.completed_targets(video_key).keys())


publish_state = PublishStateStore()
