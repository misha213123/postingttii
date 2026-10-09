"""Integration with an already installed AutoTok CLI (no Docker required).

AutoTok keeps its own login sessions in the user's home directory.  We only
store the CLI alias in PostingTTII, never a copy of TikTok cookies or tokens.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path

from app.store import store

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_PUBLISHED_RE = re.compile(r"Published\.\s*Video id:\s*([^\s]+)", re.IGNORECASE)


def validate_name(name: str) -> str:
    name = name.strip()
    if not _NAME_RE.fullmatch(name):
        raise ValueError("Имя AutoTok: 1–64 символа (латиница, цифры, _, -, .)")
    return name


def executable() -> str | None:
    """uv tools are installed here on Windows even before PATH is updated."""
    found = shutil.which("autotok")
    if found:
        return found
    for candidate in (
        Path.home() / ".local" / "bin" / "autotok.exe",
        Path.home() / ".local" / "bin" / "autotok",
    ):
        if candidate.is_file():
            return str(candidate)
    return None


async def run_cli(*args: str, timeout: int = 90) -> tuple[int, str]:
    binary = executable()
    if not binary:
        raise RuntimeError("AutoTok не найден. Установи через: uv tool install autotok")
    process = await asyncio.create_subprocess_exec(
        binary, *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        raise RuntimeError("AutoTok: превышено время ожидания. Проверь TikTok вручную.")
    return process.returncode or 0, output.decode("utf-8", errors="replace")


async def check_account(name: str) -> None:
    name = validate_name(name)
    code, output = await run_cli("accounts", "check", name, timeout=90)
    if code or "session OK" not in output:
        raise RuntimeError("Сессия TikTok не подтверждена: " + output[-800:])


async def publish(slot: int, video_path: Path, caption: str) -> dict:
    account = store.get("tiktok", slot)
    if not account or account.get("provider") != "autotok":
        raise RuntimeError("Этот TikTok не подключён через AutoTok")
    alias = validate_name(str(account.get("autotok_account", "")))
    if not video_path.is_file():
        raise RuntimeError("Видео не найдено: " + str(video_path))

    # Only the second account takes the separate UI path, with the existing
    # AutoTok session. Never try both uploaders for the same video: a failed
    # publication acknowledgment must not trigger a duplicate post.
    browser_mode = os.getenv("TIKTOK_ACCOUNT2_BROWSER", "1").strip().lower()
    if slot == 2 and alias == "account2" and browser_mode in {"1", "true", "yes", "on"}:
        from app.services.tiktok_browser_upload import publish_browser_account2
        return await asyncio.to_thread(publish_browser_account2, video_path, caption, alias)

    # -vi 0 = public.  AutoTok is called as a child process, not as a shell.
    # Never retry automatically on an ambiguous result: that could duplicate posts.
    code, output = await run_cli(
        "upload", "-u", alias, "-v", str(video_path),
        "-t", caption[:2200], "-vi", "0",
        timeout=1800,
    )
    if code:
        raise RuntimeError("AutoTok upload failed: " + output[-1200:])
    match = _PUBLISHED_RE.search(output)
    if not match:
        raise RuntimeError(
            "AutoTok завершился, но подтверждение Published не найдено. "
            "Проверь публикацию вручную перед повтором. " + output[-600:]
        )
    return {
        "id": match.group(1),
        "platform": "tiktok",
        "provider": "autotok",
        "slot": slot,
        "visibility": "public",
    }
