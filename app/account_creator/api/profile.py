from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.account_creator import database
from app.account_creator.services.profile_service import (
    create_avatar_svg,
    generate_creator_profile,
)

router = APIRouter(prefix="/api/account-manager", tags=["account-manager-profile"])


class ProfileUpdateRequest(BaseModel):
    display_name: str = Field(min_length=2, max_length=120)
    username: str = Field(min_length=3, max_length=64)
    bio: str = Field(default="", max_length=500)


def _with_avatar_url(account: dict) -> dict:
    account = dict(account)
    account["avatar_url"] = f"/api/account-manager/accounts/{account['id']}/avatar"
    return account


@router.post("/accounts/{account_id}/profile/generate")
def generate_profile(account_id: int):
    account = database.get_account(account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    if not account.get("email"):
        raise HTTPException(409, "Сначала назначь email alias")

    generated = None
    for _ in range(20):
        candidate = generate_creator_profile()
        if not database.username_exists(
            candidate["username"],
            exclude_account_id=account_id,
        ):
            generated = candidate
            break

    if not generated:
        raise HTTPException(500, "Не удалось подобрать уникальный username")

    avatar = create_avatar_svg(
        account_id,
        generated["display_name"],
        generated["username"],
    )

    try:
        saved = database.save_creator_profile(
            account_id,
            display_name=generated["display_name"],
            username=generated["username"],
            bio=generated["bio"],
            avatar_path=str(avatar),
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc

    if not saved:
        raise HTTPException(404, "Account not found")

    return _with_avatar_url(saved)


@router.put("/accounts/{account_id}/profile")
def update_profile(account_id: int, body: ProfileUpdateRequest):
    account = database.get_account(account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    if not account.get("email"):
        raise HTTPException(409, "Сначала назначь email alias")

    if database.username_exists(
        body.username,
        exclude_account_id=account_id,
    ):
        raise HTTPException(409, "Такой username уже используется в Account Manager")

    avatar = create_avatar_svg(
        account_id,
        body.display_name,
        body.username,
    )

    try:
        saved = database.save_creator_profile(
            account_id,
            display_name=body.display_name,
            username=body.username,
            bio=body.bio,
            avatar_path=str(avatar),
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc

    if not saved:
        raise HTTPException(404, "Account not found")

    return _with_avatar_url(saved)


@router.get("/accounts/{account_id}/avatar")
def account_avatar(account_id: int):
    account = database.get_account(account_id)
    if not account:
        raise HTTPException(404, "Account not found")

    path = account.get("avatar_path") or ""
    if not path:
        raise HTTPException(404, "Avatar not generated")

    from pathlib import Path

    avatar_path = Path(path)
    if not avatar_path.exists() or not avatar_path.is_file():
        raise HTTPException(404, "Avatar file not found")

    return FileResponse(
        avatar_path,
        media_type="image/svg+xml",
        filename=avatar_path.name,
        content_disposition_type="inline",
    )
