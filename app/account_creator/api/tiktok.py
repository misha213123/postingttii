from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.account_creator import database
from app.account_creator.platforms.tiktok import (
    TikTokSetupError,
    advance_signup,
    close_session,
    start_signup,
)
from app.account_creator.services.credential_service import (
    decrypt_secret,
    encrypt_secret,
    generate_password,
)

router = APIRouter(prefix="/api/account-manager", tags=["account-manager-tiktok"])


def _ensure_password(account_id: int) -> str:
    encrypted = database.get_account_password_encrypted(account_id)
    if encrypted:
        return decrypt_secret(encrypted)

    password = generate_password()
    database.set_account_password(account_id, encrypt_secret(password))
    return password


def _validate_ready(account: dict) -> None:
    if not account.get("email"):
        raise HTTPException(409, "Сначала назначь email alias")
    if not account.get("profile_ready"):
        raise HTTPException(409, "Сначала создай Creator profile")


def _persist_result(account_id: int, result: dict):
    state = str(result.get("state") or "MANUAL_STEP")

    if state == "DONE":
        return database.set_platform_state(
            account_id,
            "tiktok",
            social_status="CONNECTED",
            account_status="TIKTOK_DONE",
            step="DONE",
            job_status="DONE",
        )

    if state in {
        "MANUAL_CAPTCHA",
        "MANUAL_VERIFICATION",
        "MANUAL_BIRTHDAY",
        "MANUAL_STEP",
        "FORM_FILLED",
        "PROFILE_FILLED",
    }:
        return database.set_platform_state(
            account_id,
            "tiktok",
            social_status="ACTION_REQUIRED",
            account_status="TIKTOK_WAITING_USER",
            step=state,
            job_status="WAITING_USER",
        )

    return database.set_platform_state(
        account_id,
        "tiktok",
        social_status="CREATING",
        account_status="TIKTOK_STARTED",
        step=state,
        job_status="RUNNING",
    )


@router.post("/accounts/{account_id}/tiktok/start")
async def start_tiktok(account_id: int):
    account = database.get_account(account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    _validate_ready(account)

    password = _ensure_password(account_id)
    database.set_platform_state(
        account_id,
        "tiktok",
        social_status="CREATING",
        account_status="TIKTOK_STARTED",
        step="OPEN_SIGNUP",
        job_status="RUNNING",
    )

    try:
        result = await start_signup(account, password)
    except TikTokSetupError as exc:
        database.set_platform_state(
            account_id,
            "tiktok",
            social_status="FAILED",
            account_status="FAILED",
            step="OPEN_SIGNUP",
            job_status="FAILED",
            error=str(exc),
        )
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        detail = str(exc).strip() or exc.__class__.__name__
        database.set_platform_state(
            account_id,
            "tiktok",
            social_status="FAILED",
            account_status="FAILED",
            step="OPEN_SIGNUP",
            job_status="FAILED",
            error=detail,
        )
        raise HTTPException(500, f"TikTok automation error: {detail}") from exc

    updated = _persist_result(account_id, result)
    return {"result": result, "account": updated}


@router.post("/accounts/{account_id}/tiktok/continue")
async def continue_tiktok(account_id: int):
    account = database.get_account(account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    _validate_ready(account)

    password = _ensure_password(account_id)

    try:
        result = await advance_signup(account, password)
    except TikTokSetupError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        detail = str(exc).strip() or exc.__class__.__name__
        raise HTTPException(500, f"TikTok automation error: {detail}") from exc

    updated = _persist_result(account_id, result)
    return {"result": result, "account": updated}


@router.post("/accounts/{account_id}/tiktok/close")
async def close_tiktok(account_id: int):
    account = database.get_account(account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    await close_session(account_id)
    return {"ok": True, "message": "TikTok automation browser закрыт"}
