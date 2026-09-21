from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException

from app.account_creator import database
from app.account_creator.services.browser_service import (
    BrowserProfileError,
    close_profile,
    open_profile,
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


def _open_tiktok(account: dict) -> dict:
    account_id = int(account["id"])
    preferred = os.getenv("TIKTOK_BROWSER", "edge").strip().lower() or "edge"

    # Close only a browser window previously launched by PostingTTII for this
    # Account. The next launch is a normal Edge/Chrome process, not Playwright.
    close_profile(account_id)

    try:
        result = open_profile(
            account_id,
            str(account["browser_profile_path"]),
            "tiktok",
            preferred=preferred,
        )
    except BrowserProfileError as exc:
        raise HTTPException(503, str(exc)) from exc

    updated = database.set_platform_state(
        account_id,
        "tiktok",
        social_status="ACTION_REQUIRED",
        account_status="TIKTOK_WAITING_USER",
        step="MANUAL_REGISTRATION",
        job_status="WAITING_USER",
    )

    return {
        "result": {
            "state": "MANUAL_REGISTRATION",
            "message": (
                "TikTok открыт в обычном браузере. Зарегистрируйся вручную, "
                "используя Email/Password из PostingTTII. После успешного входа "
                "нажми Mark Connected."
            ),
            "browser": result.get("message", ""),
        },
        "account": updated,
    }


@router.get("/accounts/{account_id}/tiktok/credentials")
def tiktok_credentials(account_id: int):
    account = database.get_account(account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    _validate_ready(account)

    return {
        "email": account["email"],
        "password": _ensure_password(account_id),
    }


@router.post("/accounts/{account_id}/tiktok/start")
def start_tiktok(account_id: int):
    account = database.get_account(account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    _validate_ready(account)
    _ensure_password(account_id)
    return _open_tiktok(account)


@router.post("/accounts/{account_id}/tiktok/continue")
def continue_tiktok(account_id: int):
    # Backward-compatible endpoint: reopen the same normal browser profile.
    account = database.get_account(account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    _validate_ready(account)
    _ensure_password(account_id)
    return _open_tiktok(account)


@router.post("/accounts/{account_id}/tiktok/mark-connected")
def mark_tiktok_connected(account_id: int):
    account = database.get_account(account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    _validate_ready(account)

    updated = database.set_platform_state(
        account_id,
        "tiktok",
        social_status="CONNECTED",
        account_status="TIKTOK_DONE",
        step="DONE",
        job_status="DONE",
    )
    return {
        "ok": True,
        "message": "TikTok отмечен как подключённый",
        "account": updated,
    }


@router.post("/accounts/{account_id}/tiktok/close")
def close_tiktok(account_id: int):
    account = database.get_account(account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    return close_profile(account_id)
