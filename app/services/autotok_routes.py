"""Local-only AutoTok account management for the existing PostingTTII dashboard."""
from __future__ import annotations

import asyncio
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from app.store import store
from app.services import autotok_bridge

router = APIRouter(prefix="/api/autotok", tags=["autotok"])
LOGINS: dict[int, dict] = {}


class AccountRequest(BaseModel):
    slot: int
    name: str


def _local_only(request: Request) -> None:
    if not request.client or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "Подключать TikTok-аккаунты можно только с локального компьютера")


def _slot(slot: int) -> None:
    if slot not in (1, 2):
        raise HTTPException(400, "Поддерживаются TikTok-слоты 1 и 2")


def _no_duplicate(slot: int, name: str) -> None:
    current = store.get("tiktok", slot)
    if current:
        raise HTTPException(409, "Этот слот уже занят. Сначала отключи текущий TikTok-аккаунт")
    for other_slot in (1, 2):
        if slot == other_slot:
            continue
        other = store.get("tiktok", other_slot) or {}
        if other.get("provider") == "autotok" and other.get("autotok_account") == name:
            raise HTTPException(409, "Этот AutoTok-аккаунт уже привязан к другому слоту")
        pending = LOGINS.get(other_slot, {})
        if pending.get("status") == "running" and pending.get("name") == name:
            raise HTTPException(409, "Этот AutoTok-аккаунт уже подключается")


def _save(slot: int, name: str) -> None:
    store.save("tiktok", slot, {
        "provider": "autotok",
        "autotok_account": name,
        "label": name,
        "id": name,
    })


@router.get("/health")
async def autotok_health(request: Request):
    """Read-only local preflight; no login, upload or cookies are exposed."""
    _local_only(request)
    binary = autotok_bridge.executable()
    return {
        "installed": bool(binary),
        "login_supported": bool(binary),
        "hint": (
            "AutoTok доступен. Для входа выбери свободный TikTok-слот."
            if binary else
            "AutoTok не установлен. Выполни uv tool install autotok "
            "и autotok install-browser, затем перезапусти PostingTTII."
        ),
    }


@router.post("/verify/{slot}")
async def verify_connected(slot: int, request: Request):
    """Verify one connected AutoTok account before selecting it for a job."""
    _local_only(request)
    _slot(slot)
    account = store.get("tiktok", slot)
    if not account:
        raise HTTPException(404, "Сначала подключи TikTok-аккаунт в этом слоте")
    if account.get("provider") != "autotok":
        raise HTTPException(
            400, "Этот слот подключён через TikTok OAuth, не через AutoTok",
        )
    alias = autotok_bridge.validate_name(str(account.get("autotok_account") or ""))
    try:
        await autotok_bridge.check_account(alias)
    except RuntimeError as exc:
        raise HTTPException(
            409, "TikTok не подтверждает сохранённую сессию. "
                 "Нужен повторный вход вручную. " + str(exc),
        ) from None
    return {"ok": True, "slot": slot, "name": alias, "ready": True}


@router.post("/import")
async def import_existing(body: AccountRequest, request: Request):
    _local_only(request)
    _slot(body.slot)
    try:
        name = autotok_bridge.validate_name(body.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _no_duplicate(body.slot, name)
    try:
        await autotok_bridge.check_account(name)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    _save(body.slot, name)
    return {"ok": True, "slot": body.slot, "name": name}


async def _complete_login(slot: int, name: str, process) -> None:
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=600)
        if process.returncode:
            raise RuntimeError(
                "Вход не завершился: " + output.decode("utf-8", errors="replace")[-700:]
            )
        await autotok_bridge.check_account(name)
        # A slot may have been manually connected while the browser was open.
        if store.get("tiktok", slot):
            raise RuntimeError("Слот уже занят. Новый вход не импортирован.")
        _save(slot, name)
        LOGINS[slot] = {"status": "done", "name": name}
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        LOGINS[slot] = {"status": "error", "name": name, "error": "Время входа истекло"}
    except Exception as exc:
        LOGINS[slot] = {"status": "error", "name": name, "error": str(exc)[:700]}


@router.post("/login")
async def start_login(body: AccountRequest, request: Request):
    _local_only(request)
    _slot(body.slot)
    try:
        name = autotok_bridge.validate_name(body.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if LOGINS.get(body.slot, {}).get("status") == "running":
        raise HTTPException(409, "Вход для этого слота уже запущен")
    _no_duplicate(body.slot, name)
    binary = autotok_bridge.executable()
    if not binary:
        raise HTTPException(503, "AutoTok не найден. Выполни: uv tool install autotok; autotok install-browser")
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, "login", "-n", name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        raise HTTPException(503, f"Не удалось запустить AutoTok: {exc}") from exc
    LOGINS[body.slot] = {"status": "running", "name": name}
    asyncio.create_task(_complete_login(body.slot, name, proc))
    return {"ok": True, "status": "running", "name": name}


@router.get("/login/{slot}")
async def login_status(slot: int, request: Request):
    _local_only(request)
    _slot(slot)
    return LOGINS.get(slot, {"status": "idle"})
