"""Local-only AutoTok account management for the existing PostingTTII dashboard."""
from __future__ import annotations

import asyncio
import re
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from app.store import store
from app.services import autotok_bridge

router = APIRouter(prefix="/api/autotok", tags=["autotok"])
LOGINS: dict[int, dict] = {}


class AccountRequest(BaseModel):
    slot: int
    name: str

class TikTokIdentityRequest(BaseModel):
    username: str = ""


def _normalize_tiktok_username(raw: str) -> str:
    """User-reported handle, never presented as verified by TikTok."""
    value = raw.strip()
    if not value:
        return ""
    value = re.sub(r"^https?://(?:www\.)?tiktok\.com/@", "", value, flags=re.I)
    value = value.split("?", 1)[0].rstrip("/")
    value = value.lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9._]{2,24}", value) or value.endswith("."):
        raise HTTPException(
            400, "Укажи @username TikTok или ссылку https://www.tiktok.com/@username",
        )
    return value



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


@router.put("/identity/{slot}")
async def set_tiktok_identity(slot: int, body: TikTokIdentityRequest, request: Request):
    """Label the actual handle manually, without claiming identity verification."""
    _local_only(request)
    _slot(slot)
    account = store.get("tiktok", slot)
    if not account:
        raise HTTPException(404, "TikTok-слот пока не подключён")
    handle = _normalize_tiktok_username(body.username)
    payload = {k: v for k, v in account.items() if k != "slot"}
    if handle:
        payload["tiktok_username"] = handle
    else:
        payload.pop("tiktok_username", None)
    store.save("tiktok", slot, payload)
    return {
        "ok": True,
        "slot": slot,
        "username": handle,
        "source": "manual" if handle else "",
        "profile_url": f"https://www.tiktok.com/@{handle}" if handle else "",
        "note": "Имя профиля указано вручную и не проверено автоматически",
    }


@router.get("/health")
async def autotok_health(request: Request):
    """Read-only local preflight; no login, upload or cookies are exposed."""
    _local_only(request)
    binary = autotok_bridge.executable()
    version = ""
    if binary:
        try:
            exit_code, result = await autotok_bridge.run_cli("--version", timeout=12)
            if exit_code == 0:
                version = result.strip().splitlines()[0][:80] if result.strip() else ""
        except (RuntimeError, OSError, asyncio.TimeoutError, IndexError):
            pass
    return {
        "installed": bool(binary),
        "login_supported": bool(binary),
        "version": version,
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
    return {"ok": True, "slot": slot, "name": alias, "ready": True, "identity_verified": False}


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


async def _complete_login(slot: int, name: str, process, *, reconnect: bool = False) -> None:
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=600)
        if process.returncode:
            raise RuntimeError(
                "Вход не завершился: AutoTok завершился с ошибкой. "
                "Проверь открывшееся окно TikTok и выполни вход вручную."
            )
        await autotok_bridge.check_account(name)
        # Reconnect keeps the existing slot; new login fills an empty slot.
        existing = store.get("tiktok", slot)
        if reconnect:
            if not existing or existing.get("provider") != "autotok" or (
                existing.get("autotok_account") != name
            ):
                raise RuntimeError("Подключённый аккаунт был изменён во время входа")
        elif existing:
            raise RuntimeError("Слот уже занят. Новый вход не импортирован.")
        else:
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


@router.post("/relogin/{slot}")
async def relogin_account(slot: int, request: Request):
    """Manually re-authorize an existing AutoTok account in a visible browser."""
    _local_only(request)
    _slot(slot)
    account = store.get("tiktok", slot)
    if not account or account.get("provider") != "autotok":
        raise HTTPException(404, "В этом слоте нет подключённого AutoTok-аккаунта")
    alias = autotok_bridge.validate_name(str(account.get("autotok_account") or ""))
    if LOGINS.get(slot, {}).get("status") == "running":
        raise HTTPException(409, "Для этого слота уже открыт процесс входа")
    binary = autotok_bridge.executable()
    if not binary:
        raise HTTPException(
            503, "AutoTok не найден. Установи: uv tool install autotok; "
                 "autotok install-browser",
        )
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, "login", "-n", alias, "--force",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError:
        raise HTTPException(503, "Не удалось запустить браузерный вход AutoTok") from None
    LOGINS[slot] = {"status": "running", "name": alias, "reconnect": True}
    asyncio.create_task(_complete_login(slot, alias, proc, reconnect=True))
    return {"ok": True, "slot": slot, "name": alias, "status": "running"}


@router.get("/login/{slot}")
async def login_status(slot: int, request: Request):
    _local_only(request)
    _slot(slot)
    return LOGINS.get(slot, {"status": "idle"})
