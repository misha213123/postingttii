"""Manual Instagram Camoufox launcher; Meta publishing is intentionally separate."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from app.config import settings
from app.account_creator.services.browser_service import is_running as edge_profile_running
from app.account_creator.services.camoufox_proxy import launch_proxy

_PROCESSES: dict[int, subprocess.Popen] = {}
_STOP_FILES: dict[int, Path] = {}


class InstagramBrowserError(RuntimeError):
    pass


def camoufox_available() -> bool:
    return importlib.util.find_spec("camoufox") is not None


def profile_directory(account_id: int) -> Path:
    """Never reuse an Edge/Chromium user_data_dir for Firefox/Camoufox."""
    return (settings.data_dir / "camoufox_profiles" / f"account_{int(account_id)}").resolve()


def is_running(account_id: int) -> bool:
    process = _PROCESSES.get(account_id)
    if not process:
        return False
    if process.poll() is None:
        return True
    _PROCESSES.pop(account_id, None)
    _STOP_FILES.pop(account_id, None)
    return False


def _launch_instagram(
    account_id: int,
    profile_path: str,
    *,
    mode: str,
    email: str = "",
) -> dict[str, str | bool]:
    if is_running(account_id):
        return {
            "ok": True, "running": True,
            "message": "Camoufox уже открыт для этого аккаунта",
        }
    if edge_profile_running(account_id):
        raise InstagramBrowserError(
            "Для этого аккаунта уже открыт Edge/Chrome. Сначала закрой его окно."
        )
    if not camoufox_available():
        raise InstagramBrowserError(
            'Camoufox не установлен. Выполни .venv\\Scripts\\python.exe -m pip install -r requirements.txt '
            'и затем .venv\\Scripts\\python.exe -m camoufox fetch'
        )

    try:
        proxy_settings = launch_proxy(account_id)
    except Exception as exc:
        raise InstagramBrowserError("Не удалось прочитать настройки прокси Camoufox") from exc
    if not proxy_settings:
        raise InstagramBrowserError(
            "Сначала укажи прокси для этого аккаунта в панели «Instagram · постоянные профили»."
        )

    # profile_path is intentionally not reused: it points to Chromium's old
    # profile directory in Account Manager. Those cookies stay untouched.
    profile = profile_directory(account_id)
    profile.mkdir(parents=True, exist_ok=True)
    stop_file = profile.parent / f"account_{int(account_id)}.stop"
    stop_file.unlink(missing_ok=True)

    cmd = [
        sys.executable,
        "-m", "app.account_creator.services.instagram_browser_worker",
        "--profile", str(profile),
        "--stop-file", str(stop_file),
        "--mode", mode,
    ]
    if email.strip():
        cmd.extend(["--email", email.strip()])
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0

    # Only runtime logs, never cookies or credentials, are committed.
    log_dir = settings.data_dir / "camoufox_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"account_{int(account_id)}.log"
    try:
        with log_path.open("ab") as error_log:
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=error_log,
                creationflags=flags,
            )
            assert process.stdin is not None
            try:
                process.stdin.write(json.dumps(proxy_settings).encode("utf-8"))
                process.stdin.close()
            except (OSError, ValueError):
                process.terminate()
                raise
    except (OSError, ValueError) as exc:
        raise InstagramBrowserError(f"Не удалось запустить Camoufox: {type(exc).__name__}") from exc

    _PROCESSES[account_id] = process
    _STOP_FILES[account_id] = stop_file
    time.sleep(0.5)
    if process.poll() is not None:
        _PROCESSES.pop(account_id, None)
        _STOP_FILES.pop(account_id, None)
        raise InstagramBrowserError(
            f"Camoufox завершился сразу. Проверь установку браузера и журнал: {log_path}"
        )

    return {
        "ok": True, "running": True,
        "message": "Camoufox запускается с сохранённым профилем. Вход в Instagram выполни вручную.",
    }


def start_instagram_browser(
    account_id: int,
    profile_path: str,
    email: str,
) -> dict[str, str | bool]:
    if not email.strip():
        raise InstagramBrowserError("Для регистрации Instagram нужен email alias")
    return _launch_instagram(account_id, profile_path, mode="signup", email=email)


def open_instagram_account(account_id: int, profile_path: str) -> dict[str, str | bool]:
    return _launch_instagram(account_id, profile_path, mode="home")


def stop_instagram_browser(account_id: int) -> dict[str, str | bool]:
    process = _PROCESSES.get(account_id)
    if not process or process.poll() is not None:
        _PROCESSES.pop(account_id, None)
        _STOP_FILES.pop(account_id, None)
        return {"ok": True, "running": False, "message": "Camoufox не запущен"}

    stop_file = _STOP_FILES.get(account_id)
    if not stop_file:
        raise InstagramBrowserError("Неизвестный файл управления Camoufox")
    stop_file.touch()
    try:
        process.wait(timeout=12)
    except subprocess.TimeoutExpired:
        # Never taskkill /F a live Firefox profile: profile and cookies could corrupt.
        return {
            "ok": False, "running": True,
            "message": "Camoufox ещё закрывается. Закрой окно обычным способом.",
        }
    _PROCESSES.pop(account_id, None)
    _STOP_FILES.pop(account_id, None)
    return {"ok": True, "running": False, "message": "Camoufox закрыт; профиль сохранён"}
