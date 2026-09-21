from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.account_creator import database
from app.account_creator.services.browser_service import (
    BrowserProfileError,
    close_profile,
    is_running,
    open_profile,
    status as browser_status,
)
from app.account_creator.services.instagram_service import (
    is_running as is_instagram_running,
    stop_instagram_browser,
)

router = APIRouter(prefix="/api/account-manager", tags=["account-manager-browser"])


class BrowserOpenRequest(BaseModel):
    target: Literal["home", "tiktok", "instagram", "youtube"] = "home"


@router.get("/browser/status")
def status():
    return browser_status()


@router.get("/browser/profiles")
def profiles():
    accounts = database.list_accounts()
    return {
        "browser": browser_status(),
        "profiles": [
            {
                "account_id": account["id"],
                "account_number": account["account_number"],
                "display_name": account["display_name"],
                "username": account["username"],
                "email": account["email"],
                "status": account["status"],
                "browser_profile_path": account["browser_profile_path"],
                "running": is_running(account["id"]) or is_instagram_running(account["id"]),
                "instagram_running": is_instagram_running(account["id"]),
            }
            for account in accounts
        ],
    }


@router.post("/accounts/{account_id}/browser/open")
def open_browser(account_id: int, body: BrowserOpenRequest):
    account = database.get_account(account_id)
    if not account:
        raise HTTPException(404, "Account not found")

    try:
        return open_profile(
            account_id,
            account["browser_profile_path"],
            body.target,
        )
    except BrowserProfileError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/accounts/{account_id}/browser/close")
def close_browser(account_id: int):
    account = database.get_account(account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    stop_instagram_browser(account_id)
    return close_profile(account_id)
