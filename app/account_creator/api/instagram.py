from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.account_creator import database
from app.account_creator.services import AddyError, addy_service
from app.account_creator.services.instagram_service import (
    InstagramBrowserError,
    is_running,
    open_instagram_account,
    start_instagram_browser,
    stop_instagram_browser,
)


router = APIRouter(prefix="/api/account-manager", tags=["account-manager-instagram"])


def _alias_email(item: dict[str, Any]) -> str:
    return str(item.get("email") or item.get("address") or "").strip()


async def _ensure_account_alias(account_id: int) -> dict[str, Any]:
    account = database.get_account(account_id)
    if not account:
        raise HTTPException(404, "Account not found")

    if account.get("email"):
        return account

    if not addy_service.configured:
        raise HTTPException(503, "ADDY_API_TOKEN не настроен")

    try:
        aliases = await addy_service.list_aliases(page_size=100)
    except AddyError as exc:
        raise HTTPException(502, str(exc)) from exc

    used_aliases = database.alias_assignments()
    for item in reversed(aliases):
        alias_id = str(item.get("id") or "").strip()
        email = _alias_email(item)
        active = bool(item.get("active", True))
        if not active or not alias_id or not email or alias_id in used_aliases:
            continue

        try:
            assigned = database.assign_alias(account_id, alias_id, email)
        except ValueError:
            continue
        if assigned:
            return assigned

    raise HTTPException(
        409,
        "В addy.io нет свободного alias. Создай новый alias или освободи существующий.",
    )


@router.get("/accounts/{account_id}/instagram/status")
def instagram_status(account_id: int):
    account = database.get_account(account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    return {
        "running": is_running(account_id),
        "email": account.get("email") or "",
        "status": account.get("social_accounts", {}).get("instagram", {}).get(
            "status", "NOT_CREATED"
        ),
    }


@router.post("/accounts/{account_id}/instagram/start")
async def start_instagram(account_id: int):
    account = await _ensure_account_alias(account_id)

    try:
        result = start_instagram_browser(
            account_id,
            str(account["browser_profile_path"]),
            str(account["email"]),
        )
    except InstagramBrowserError as exc:
        raise HTTPException(503, str(exc)) from exc

    updated = database.set_platform_state(
        account_id,
        "instagram",
        social_status="ACTION_REQUIRED",
        account_status="INSTAGRAM_WAITING_USER",
        step="EDGE_REGISTRATION",
        job_status="WAITING_USER",
    )

    return {
        "result": result,
        "account": updated,
        "email": account["email"],
    }


@router.post("/accounts/{account_id}/instagram/open")
def open_instagram(account_id: int):
    account = database.get_account(account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    if not account.get("email"):
        raise HTTPException(409, "Сначала запусти регистрацию Instagram")

    try:
        result = open_instagram_account(
            account_id,
            str(account["browser_profile_path"]),
        )
    except InstagramBrowserError as exc:
        raise HTTPException(503, str(exc)) from exc

    return {
        "result": result,
        "account": account,
    }


@router.post("/accounts/{account_id}/instagram/mark-connected")
def mark_instagram_connected(account_id: int):
    account = database.get_account(account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    if not account.get("email"):
        raise HTTPException(409, "Сначала назначь email alias")

    updated = database.set_platform_state(
        account_id,
        "instagram",
        social_status="CONNECTED",
        account_status="READY",
        step="DONE",
        job_status="DONE",
    )

    return {
        "ok": True,
        "message": "Instagram отмечен как подключённый",
        "account": updated,
    }


@router.post("/accounts/{account_id}/instagram/close")
def close_instagram(account_id: int):
    account = database.get_account(account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    return stop_instagram_browser(account_id)
