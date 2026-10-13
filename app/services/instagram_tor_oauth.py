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
    if slot not in (1, 2):
        raise TorOAuthBrowserError("Для Instagram доступны только слоты 1 и 2")

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

    other = 2 if slot == 1 else 1
    other_raw = os.getenv(f"INSTAGRAM_TOR_BROWSER_{other}", "").strip().strip('"')
    if other_raw:
        other_root = Path(os.path.expandvars(other_raw)).expanduser().resolve()
        if other_root.is_file() and other_root.name.lower() == "firefox.exe":
            other_root = other_root.parent.parent
        if os.path.normcase(str(root)).lower() == os.path.normcase(str(other_root)).lower():
            raise TorOAuthBrowserError(
                "Каждому Instagram-слоту нужна отдельная установка Tor Browser: "
                "сейчас оба пути совпадают"
            )

    return root, executable


def open_tor_oauth(slot: int, local_oauth_url: str) -> None:
    """Open the local OAuth bridge in the Tor Browser assigned to this slot."""
    if os.name != "nt":
        raise TorOAuthBrowserError(
            "Этот режим рассчитан на Windows; обычная авторизация Instagram остаётся доступна"
        )

    _, executable = _tor_installation(slot)
    try:
        subprocess.Popen(
            [str(executable), "--no-remote", local_oauth_url],
            cwd=str(executable.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except OSError as exc:
        raise TorOAuthBrowserError(f"Не удалось запустить Tor Browser: {exc}") from exc
