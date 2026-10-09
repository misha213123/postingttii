"""Persistent, expiring public media files used by Instagram's Meta fetchers.

Meta fetches /media/{token} asynchronously. A plain in-memory dict loses
these URLs when PostingTTII restarts. Keep a local hardlink (or copy) and a
small on-disk index so the same token survives a restart and video archiving.

These files are intentionally public to anyone who knows the unguessable URL,
for up to 24 hours. They live under the git-ignored data directory.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import threading
import time
from pathlib import Path

from app.config import settings


class PersistentMediaRegistry:
    _TOKEN = re.compile(r"^[A-Za-z0-9_-]{32}$")
    _SUFFIXES = {".jpg", ".jpeg", ".mp4", ".mov", ".m4v", ".webm"}
    _TTL_SECONDS = 24 * 60 * 60

    def __init__(self) -> None:
        self.root = settings.data_dir / "meta_media"
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_file = self.root / "index.json"
        self.lock = threading.RLock()
        self.records: dict[str, dict] = {}
        if self.index_file.exists():
            try:
                loaded = json.loads(self.index_file.read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    raise ValueError("index is not an object")
                self.records = loaded
            except (ValueError, OSError) as exc:
                # Never silently delete files on a corrupt index: report the
                # problem rather than breaking pending Meta fetch URLs.
                raise RuntimeError(
                    f"Невозможно прочитать индекс публичных MP4: {self.index_file}"
                ) from exc
        with self.lock:
            self._prune_locked()

    def _path(self, token: str, record: dict) -> Path | None:
        if not self._TOKEN.fullmatch(token):
            return None
        if not isinstance(record, dict):
            return None
        suffix = record.get("suffix")
        if suffix not in self._SUFFIXES:
            return None
        return self.root / (token + suffix)

    def _write_locked(self) -> None:
        temp = self.root / "index.json.tmp"
        temp.write_text(
            json.dumps(self.records, ensure_ascii=False), encoding="utf-8"
        )
        temp.replace(self.index_file)

    def _prune_locked(self) -> None:
        now = time.time()
        changed = False
        for token, record in list(self.records.items()):
            path = self._path(token, record)
            if (
                path is None
                or not isinstance(record.get("expires_at"), (int, float))
                or record["expires_at"] <= now
                or not path.is_file()
            ):
                if path is not None and record.get("expires_at", 0) <= now:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
                self.records.pop(token, None)
                changed = True
        if changed:
            self._write_locked()

    def __setitem__(self, token: str, source: Path) -> None:
        """Stage a stable file before its URL is submitted to Meta."""
        if not self._TOKEN.fullmatch(token):
            raise ValueError("Некорректный токен видео")
        src = Path(source).resolve(strict=True)
        valid_roots = (
            settings.upload_dir.resolve(),
            settings.posted_dir.resolve(),
            (settings.data_dir / "instagram_covers").resolve(),
        )
        if not src.is_file() or not any(
            src.is_relative_to(root) for root in valid_roots
        ):
            raise ValueError("Источники Meta разрешены только из папок PostingTTII")
        suffix = src.suffix.lower()
        if suffix not in self._SUFFIXES:
            raise ValueError("Неподдерживаемый формат публичного видео/обложки")
        destination = self.root / (token + suffix)
        with self.lock:
            self._prune_locked()
            if token in self.records or destination.exists():
                raise ValueError("Этот токен медиа уже используется")
            try:
                # On the same NTFS volume, this preserves the exact MP4 without
                # duplicating tens of MB and survives moving the inbox file.
                try:
                    os.link(src, destination)
                except OSError:
                    # If hardlinks are unavailable, use an ordinary copy.
                    shutil.copy2(src, destination)
                self.records[token] = {
                    "suffix": suffix,
                    "expires_at": time.time() + self._TTL_SECONDS,
                }
                self._write_locked()
            except Exception:
                self.records.pop(token, None)
                destination.unlink(missing_ok=True)
                raise

    def get(self, token: str, default=None):
        if not isinstance(token, str) or not self._TOKEN.fullmatch(token):
            return default
        with self.lock:
            record = self.records.get(token)
            if not record:
                return default
            path = self._path(token, record)
            if path is None or record.get("expires_at", 0) <= time.time():
                self._prune_locked()
                return default
            return path if path.is_file() else default

    def pop(self, token: str, default=None):
        with self.lock:
            record = self.records.pop(token, None)
            if record is None:
                return default
            path = self._path(token, record)
            try:
                self._write_locked()
            except Exception:
                self.records[token] = record
                raise
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            return path or default

    def values(self) -> list[Path]:
        """Only staged paths, never originals; archive can move source files."""
        with self.lock:
            self._prune_locked()
            return [
                path
                for token, record in self.records.items()
                if (path := self._path(token, record)) is not None
            ]
