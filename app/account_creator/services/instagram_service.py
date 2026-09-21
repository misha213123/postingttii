from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

from app.account_creator.services.browser_service import close_profile, find_edge


_PROCESSES: dict[int, subprocess.Popen] = {}


class InstagramBrowserError(RuntimeError):
    pass


def is_running(account_id: int) -> bool:
    process = _PROCESSES.get(account_id)
    if not process:
        return False
    if process.poll() is None:
        return True
    _PROCESSES.pop(account_id, None)
    return False


def _launch_instagram(
    account_id: int,
    profile_path: str,
    *,
    mode: str,
    email: str = "",
) -> dict[str, str | bool]:
    if importlib.util.find_spec("playwright") is None:
        raise InstagramBrowserError(
            "Playwright не установлен. Выполни pip install -r requirements.txt и перезапусти PostingTTII"
        )

    if is_running(account_id):
        return {
            "ok": True,
            "running": True,
            "message": "Instagram Edge уже открыт для этого Account",
        }

    try:
        edge = find_edge()
    except Exception as exc:
        raise InstagramBrowserError(str(exc)) from exc

    profile = Path(profile_path).resolve()
    profile.mkdir(parents=True, exist_ok=True)
    close_profile(account_id)

    command = [
        sys.executable,
        "-m",
        "app.account_creator.services.instagram_browser_worker",
        "--edge",
        str(edge),
        "--profile",
        str(profile),
        "--mode",
        mode,
    ]
    if email.strip():
        command.extend(["--email", email.strip()])

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except OSError as exc:
        raise InstagramBrowserError(f"Не удалось открыть Edge: {exc}") from exc

    _PROCESSES[account_id] = process
    time.sleep(0.7)

    if process.poll() is not None:
        _PROCESSES.pop(account_id, None)
        raise InstagramBrowserError(
            "Edge закрылся сразу после запуска. Проверь установку Playwright и Microsoft Edge"
        )

    return {
        "ok": True,
        "running": True,
        "message": (
            "Instagram открыт в сохранённом Edge-профиле этого Account"
            if mode == "home"
            else "Instagram открыт в Edge. Email alias подставляется автоматически."
        ),
    }


def start_instagram_browser(
    account_id: int,
    profile_path: str,
    email: str,
) -> dict[str, str | bool]:
    if not email.strip():
        raise InstagramBrowserError("Для Instagram нужен email alias")
    return _launch_instagram(
        account_id,
        profile_path,
        mode="signup",
        email=email,
    )


def open_instagram_account(
    account_id: int,
    profile_path: str,
) -> dict[str, str | bool]:
    return _launch_instagram(
        account_id,
        profile_path,
        mode="home",
    )


def stop_instagram_browser(account_id: int) -> dict[str, str | bool]:
    process = _PROCESSES.get(account_id)
    if not process:
        return {
            "ok": True,
            "running": False,
            "message": "Instagram Edge не запущен через PostingTTII",
        }

    if process.poll() is None:
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            except OSError:
                pass
        else:
            try:
                process.terminate()
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass

    _PROCESSES.pop(account_id, None)
    return {
        "ok": True,
        "running": False,
        "message": "Instagram Edge остановлен",
    }
