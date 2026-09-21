from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.account_creator import database
from app.account_creator.services import AddyError, addy_service

router = APIRouter(prefix="/api/account-manager", tags=["account-manager-aliases"])


class AliasCreateRequest(BaseModel):
    domain: str = Field(default="", max_length=255)
    format: str = Field(default="random_characters", max_length=40)
    local_part: str = Field(default="", max_length=100)
    description: str = Field(default="PostingTTII", max_length=255)


class AliasAssignRequest(BaseModel):
    account_id: int
    email: str = Field(min_length=3, max_length=320)


def _alias_email(item: dict[str, Any]) -> str:
    return str(item.get("email") or item.get("address") or "").strip()


def _normalize_alias(
    item: dict[str, Any],
    assignments: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    alias_id = str(item.get("id") or "")
    assigned = assignments.get(alias_id)
    return {
        "id": alias_id,
        "email": _alias_email(item),
        "domain": str(item.get("domain") or ""),
        "description": str(item.get("description") or ""),
        "active": bool(item.get("active", True)),
        "created_at": str(item.get("created_at") or ""),
        "updated_at": str(item.get("updated_at") or ""),
        "state": "USED" if assigned else "FREE",
        "assigned_account": assigned,
    }


@router.get("/addy/status")
async def addy_status():
    if not addy_service.configured:
        return {
            "configured": False,
            "connected": False,
            "message": "ADDY_API_TOKEN не настроен",
            "domain_options": [],
            "default_domain": "",
            "default_format": "",
        }

    try:
        options = await addy_service.domain_options()
        return {
            "configured": True,
            "connected": True,
            "message": "addy.io подключён",
            "domain_options": options.get("data", []),
            "shared_domains": options.get("sharedDomains", []),
            "default_domain": options.get("defaultAliasDomain", ""),
            "default_format": options.get("defaultAliasFormat", ""),
        }
    except AddyError as exc:
        return {
            "configured": True,
            "connected": False,
            "message": str(exc),
            "domain_options": [],
            "default_domain": "",
            "default_format": "",
        }


@router.get("/aliases")
async def list_aliases():
    if not addy_service.configured:
        return {
            "configured": False,
            "aliases": [],
            "counts": {"total": 0, "free": 0, "used": 0},
        }

    try:
        aliases = await addy_service.list_aliases(page_size=100)
    except AddyError as exc:
        raise HTTPException(502, str(exc)) from exc

    assignments = database.alias_assignments()
    normalized = [_normalize_alias(item, assignments) for item in aliases]
    free = sum(1 for item in normalized if item["state"] == "FREE")
    used = len(normalized) - free
    return {
        "configured": True,
        "aliases": normalized,
        "counts": {"total": len(normalized), "free": free, "used": used},
    }


@router.post("/aliases", status_code=status.HTTP_201_CREATED)
async def create_alias(body: AliasCreateRequest):
    if not addy_service.configured:
        raise HTTPException(503, "ADDY_API_TOKEN не настроен")

    try:
        item = await addy_service.create_alias(
            domain=body.domain,
            alias_format=body.format,
            local_part=body.local_part,
            description=body.description,
        )
    except AddyError as exc:
        raise HTTPException(502, str(exc)) from exc

    return _normalize_alias(item, database.alias_assignments())


@router.post("/aliases/{alias_id}/assign")
async def assign_alias(alias_id: str, body: AliasAssignRequest):
    account = database.get_account(body.account_id)
    if not account:
        raise HTTPException(404, "Account not found")

    try:
        updated = database.assign_alias(body.account_id, alias_id, body.email)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc

    return updated


@router.post("/accounts/{account_id}/alias/unassign")
async def unassign_alias(account_id: int):
    account = database.unassign_alias(account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    return account
