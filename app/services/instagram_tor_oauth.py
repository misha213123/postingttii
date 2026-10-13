"""Optional interactive Instagram OAuth in separate Windows Tor Browser installations.

This only changes the browser used for the *OAuth login*. Media uploads continue
to use Instagram's official API and the existing encrypted token storage.

Tor Browser uses private-session cookies: browser logins are NOT expected to
survive closing Tor Browser. OAuth tokens are stored independently by PostingTTII.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


class TorOAuthBrowserError(RuntimeError):
    """Invalid local Tor Browser configuration or process launch failure."""


def tor_oauth_enabled() -> bool:
    return os.getenv("INSTAGRAM_OAUTH_BROWSER", "default").strip().lower() == "tor"


def _tor_installation(slot: int) -> tuple[Path, Path]:
    if slot not in range(1, 9):
        raise TorOAuthBrowserError("Для Instagram доступны слоты от 1 до 8")

    env_name = f"INSTAGRAM_TOR_BROWSER_{slot}"
    configured = os.getenv(env_name, "").strip().strip('"')
    if not configured:
        raise TorOAuthBrowserError(
            f"Укажи {env_name} — путь к отдельной установке Tor Browser для Instagram #{slot}"
        )

    # The Tor Browser directory is a self-contained installation with its own
    # browser profile and Tor data. Do not create generic Firefox profiles:
    # they do not inherit Tor Browser privacy settings.
    root = Path(os.path.expandvars(configured)).expanduser().resolve()
    if root.is_file() and root.name.lower() == "firefox.exe" and root.parent.name.lower() == "browser":
        root = root.parent.parent

    executable = root / "Browser" / "firefox.exe"
    profile = root / "Browser" / "TorBrowser" / "Data" / "Browser" / "profile.default"
    if not executable.is_file() or not profile.is_dir():
        raise TorOAuthBrowserError(
            f"{env_name} должен указывать на установленный Tor Browser "
            "(папку с Browser/firefox.exe и TorBrowser/Data/Browser/profile.default)"
        )

    for other in range(1, 9):
        if other == slot:
            continue
        other_raw = os.getenv(f"INSTAGRAM_TOR_BROWSER_{other}", "").strip().strip('"')
        if not other_raw:
            continue
        other_root = Path(os.path.expandvars(other_raw)).expanduser().resolve()
        if other_root.is_file() and other_root.name.lower() == "firefox.exe":
            other_root = other_root.parent.parent
        if os.path.normcase(str(root)).lower() == os.path.normcase(str(other_root)).lower():
            raise TorOAuthBrowserError(
                "Каждому Instagram-слоту нужна отдельная установка Tor Browser: "
                f"пути слотов {slot} и {other} совпадают"
            )

    return root, executable


def open_tor_oauth(slot: int, instagram_oauth_url: str) -> None:
    """Open the official Instagram OAuth URL in this slot\'s Tor Browser."""
    if os.name != "nt":
        raise TorOAuthBrowserError(
            "Этот режим рассчитан на Windows; обычная авторизация Instagram остаётся доступна"
        )

    _, executable = _tor_installation(slot)
    try:
        subprocess.Popen(
            [str(executable), "--no-remote", instagram_oauth_url],
            cwd=str(executable.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except OSError as exc:
        raise TorOAuthBrowserError(f"Не удалось запустить Tor Browser: {exc}") from exc

def tor_enabled_for_slot(slot: int) -> bool:
    if not tor_oauth_enabled():
        return False
    selected = os.getenv("INSTAGRAM_TOR_SLOTS", "").strip()
    if not selected:
        return True
    try:
        slots = {int(item.strip()) for item in selected.split(",") if item.strip()}
    except ValueError as exc:
        raise TorOAuthBrowserError("INSTAGRAM_TOR_SLOTS: укажи номера через запятую") from exc
    return slot in slots
