from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Literal


TARGET_URLS: dict[str, str] = {
    "home": "about:blank",
    "tiktok": "https://www.tiktok.com/",
    "instagram": "https://www.instagram.com/",
    "youtube": "https://www.youtube.com/",
}

_PROCESSES: dict[int, subprocess.Popen] = {}


class BrowserProfileError(RuntimeError):
    pass


def _candidate_executables() -> list[Path]:
    items: list[Path] = []
    explicit = os.getenv("BROWSER_EXECUTABLE", "").strip()
    if explicit:
        items.append(Path(explicit))

    for env_name in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        base = os.getenv(env_name, "").strip()
        if not base:
            continue
        root = Path(base)
        items.extend(
            [
                root / "Google" / "Chrome" / "Application" / "chrome.exe",
                root / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            ]
        )

    for name in ("chrome.exe", "chrome", "msedge.exe", "msedge"):
        found = shutil.which(name)
        if found:
            items.append(Path(found))

    unique: list[Path] = []
    seen: set[str] = set()
    for item in items:
        key = str(item).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def find_browser() -> Path:
    for path in _candidate_executables():
        if path.exists() and path.is_file():
            return path
    raise BrowserProfileError(
        "Chrome/Edge не найден. Установи Chrome или укажи BROWSER_EXECUTABLE в .env"
    )


def is_running(account_id: int) -> bool:
    process = _PROCESSES.get(account_id)
    if not process:
        return False
    if process.poll() is None:
        return True
    _PROCESSES.pop(account_id, None)
    return False


def open_profile(
    account_id: int,
    profile_path: str,
    target: Literal["home", "tiktok", "instagram", "youtube"] = "home",
) -> dict[str, str | bool]:
    if is_running(account_id):
        return {
            "ok": True,
            "running": True,
            "message": "Browser profile уже открыт",
        }

    browser = find_browser()
    profile = Path(profile_path).resolve()
    profile.mkdir(parents=True, exist_ok=True)
    url = TARGET_URLS.get(target, TARGET_URLS["home"])

    args = [
        str(browser),
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        url,
    ]

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    try:
        process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except OSError as exc:
        raise BrowserProfileError(f"Не удалось открыть browser profile: {exc}") from exc

    _PROCESSES[account_id] = process
    return {
        "ok": True,
        "running": True,
        "message": f"Открыт {browser.name}",
    }


def close_profile(account_id: int) -> dict[str, str | bool]:
    process = _PROCESSES.get(account_id)
    if not process:
        return {
            "ok": True,
            "running": False,
            "message": "Browser profile не запущен через PostingTTII",
        }

    if process.poll() is None:
        try:
            process.terminate()
        except OSError:
            pass

    _PROCESSES.pop(account_id, None)
    return {
        "ok": True,
        "running": False,
        "message": "Browser profile закрыт",
    }


def status() -> dict[str, str | bool]:
    try:
        browser = find_browser()
        return {
            "available": True,
            "browser": str(browser),
        }
    except BrowserProfileError as exc:
        return {
            "available": False,
            "browser": "",
            "message": str(exc),
        }
