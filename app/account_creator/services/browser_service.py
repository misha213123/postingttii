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


def _browser_paths(kind: str) -> list[Path]:
    items: list[Path] = []

    for env_name in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        base = os.getenv(env_name, "").strip()
        if not base:
            continue
        root = Path(base)
        if kind == "edge":
            items.append(root / "Microsoft" / "Edge" / "Application" / "msedge.exe")
        elif kind == "chrome":
            items.append(root / "Google" / "Chrome" / "Application" / "chrome.exe")

    names = ("msedge.exe", "msedge") if kind == "edge" else ("chrome.exe", "chrome")
    for name in names:
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


def _candidate_executables(preferred: str = "auto") -> list[Path]:
    preferred = (preferred or "auto").strip().lower()
    items: list[Path] = []

    explicit = os.getenv("BROWSER_EXECUTABLE", "").strip()
    if explicit and preferred == "auto":
        items.append(Path(explicit))

    if preferred == "edge":
        items.extend(_browser_paths("edge"))
        items.extend(_browser_paths("chrome"))
    elif preferred == "chrome":
        items.extend(_browser_paths("chrome"))
        items.extend(_browser_paths("edge"))
    else:
        items.extend(_browser_paths("chrome"))
        items.extend(_browser_paths("edge"))

    unique: list[Path] = []
    seen: set[str] = set()
    for item in items:
        key = str(item).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def find_edge() -> Path:
    for path in _browser_paths("edge"):
        if path.exists() and path.is_file():
            return path
    raise BrowserProfileError(
        "Microsoft Edge не найден. Установи Edge или проверь стандартный путь установки"
    )


def find_browser(preferred: str = "auto") -> Path:
    for path in _candidate_executables(preferred):
        if path.exists() and path.is_file():
            return path
    raise BrowserProfileError(
        "Chrome/Edge не найден. Установи браузер или укажи BROWSER_EXECUTABLE в .env"
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
    *,
    preferred: str = "auto",
) -> dict[str, str | bool]:
    if is_running(account_id):
        return {
            "ok": True,
            "running": True,
            "message": "Browser profile уже открыт",
        }

    browser = find_browser(preferred)
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
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
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
        edge = find_edge()
        edge_available = True
        edge_message = ""
    except BrowserProfileError as exc:
        edge = None
        edge_available = False
        edge_message = str(exc)

    try:
        browser = find_browser()
        return {
            "available": True,
            "browser": str(browser),
            "edge_available": edge_available,
            "edge_browser": str(edge) if edge else "",
            "edge_message": edge_message,
        }
    except BrowserProfileError as exc:
        return {
            "available": False,
            "browser": "",
            "edge_available": edge_available,
            "edge_browser": str(edge) if edge else "",
            "edge_message": edge_message,
            "message": str(exc),
        }
