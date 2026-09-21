from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.account_creator import database

router = APIRouter(prefix="/api/account-manager", tags=["account-manager"])


class AccountCreateRequest(BaseModel):
    email: str = Field(default="", max_length=320)
    display_name: str = Field(default="", max_length=120)
    username: str = Field(default="", max_length=64)
    bio: str = Field(default="", max_length=500)


class AccountUpdateRequest(BaseModel):
    email: str | None = Field(default=None, max_length=320)
    display_name: str | None = Field(default=None, max_length=120)
    username: str | None = Field(default=None, max_length=64)
    bio: str | None = Field(default=None, max_length=500)


@router.get("/accounts")
def list_accounts():
    return {
        "accounts": database.list_accounts(),
        "counts": database.account_counts(),
    }


@router.get("/accounts/{account_id}")
def get_account(account_id: int):
    account = database.get_account(account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    return account


@router.post("/accounts", status_code=status.HTTP_201_CREATED)
def create_account(body: AccountCreateRequest):
    try:
        return database.create_account(
            email=body.email,
            display_name=body.display_name,
            username=body.username,
            bio=body.bio,
        )
    except sqlite3.IntegrityError as exc:
        if "email" in str(exc).lower():
            raise HTTPException(409, "Этот email уже назначен другому Account") from exc
        raise HTTPException(409, "Не удалось создать Account: конфликт данных") from exc


@router.patch("/accounts/{account_id}")
def update_account(account_id: int, body: AccountUpdateRequest):
    fields = body.model_dump(exclude_unset=True)
    try:
        account = database.update_account(account_id, fields)
    except sqlite3.IntegrityError as exc:
        if "email" in str(exc).lower():
            raise HTTPException(409, "Этот email уже назначен другому Account") from exc
        raise HTTPException(409, "Не удалось сохранить изменения") from exc
    if not account:
        raise HTTPException(404, "Account not found")
    return account


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(account_id: int):
    if not database.delete_account(account_id):
        raise HTTPException(404, "Account not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
