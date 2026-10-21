"""Read-only Instagram performance dashboard for already-connected Meta accounts.

Account details are obtained from the existing encrypted TokenStore. Only public
metadata and aggregate counts are saved in instagram_stats.json; never tokens.
The dashboard is not involved in uploading or scheduling publications.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.store import store

router = APIRouter(prefix="/api/instagram-stats", tags=["instagram-stats"])
logger = logging.getLogger(__name__)
STATS_FILE = settings.data_dir / "instagram_stats.json"
REFRESH_SECONDS = 30 * 60
MAX_REELS = 50
MAX_INSIGHT_CALLS = 30
REFRESH_LOCK = asyncio.Lock()
MANUAL_TASK: asyncio.Task | None = None


def _read() -> dict[str, Any]:
    try:
        raw = json.loads(STATS_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("accounts"), dict):
            return raw
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return {"version": 1, "last_checked": None, "accounts": {}}


def _write(document: dict[str, Any]) -> None:
    STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATS_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, STATS_FILE)


def _number(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return n if n >= 0 else None


def _public_error(exc: Exception) -> str:
    # Do not reflect access tokens, HTTP URLs or sensitive Graph API responses.
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (400, 403):
            return f"Instagram API: доступ к данным ограничен (HTTP {code})"
        if code == 401:
            return "Токен Instagram недействителен или истёк (HTTP 401)"
        if code == 429:
            return "Instagram API временно ограничил частоту запросов (HTTP 429)"
        return f"Instagram API: HTTP {code}"
    if isinstance(exc, httpx.TimeoutException):
        return "Instagram API не ответил вовремя"
    if isinstance(exc, httpx.HTTPError):
        return "Нет соединения с Instagram API"
    return "Не удалось получить статистику"


async def _graph_get(client: httpx.AsyncClient, endpoint: str, token: str,
                     params: dict | None = None) -> dict[str, Any]:
    args = dict(params or {})
    args["access_token"] = token
    base = f"https://graph.instagram.com/{settings.instagram_graph_version}/"
    response = await client.get(base + endpoint.lstrip("/"), params=args)
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict):
        raise ValueError("Unexpected Graph API payload")
    return result


async def _profile(client: httpx.AsyncClient, token: str) -> dict[str, Any]:
    try:
        return await _graph_get(client, "me", token,
            {"fields": "user_id,username,followers_count,media_count"})
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code not in (400, 403):
            raise
        # Some account types do not expose all counters. Keep media available.
        return await _graph_get(client, "me", token, {"fields": "user_id,username"})


async def _reels(client: httpx.AsyncClient, token: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    after: str | None = None
    fields = ("id,caption,media_type,media_product_type,permalink,"
              "like_count,comments_count,timestamp,thumbnail_url")
    for _ in range(4):
        params: dict[str, Any] = {"fields": fields, "limit": 50}
        if after:
            params["after"] = after
        try:
            response = await _graph_get(client, "me/media", token, params)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (400, 403):
                raise
            # Optional fields vary by media type and Graph API version.
            fields = "id,caption,media_type,permalink,like_count,comments_count,timestamp"
            params["fields"] = fields
            try:
                response = await _graph_get(client, "me/media", token, params)
            except httpx.HTTPStatusError as second:
                if second.response.status_code not in (400, 403):
                    raise
                fields = "id,media_type,permalink,timestamp"
                params["fields"] = fields
                response = await _graph_get(client, "me/media", token, params)
        for media in response.get("data") or []:
            if not isinstance(media, dict):
                continue
            product = str(media.get("media_product_type") or "").upper()
            media_type = str(media.get("media_type") or "").upper()
            if product != "REELS" and (product or media_type != "VIDEO"):
                continue
            media_id = str(media.get("id") or "")
            if not media_id:
                continue
            link = str(media.get("permalink") or "")
            if not link.startswith(("https://www.instagram.com/", "https://instagram.com/")):
                link = ""
            result.append({
                "id": media_id, "caption": str(media.get("caption") or "")[:240],
                "timestamp": str(media.get("timestamp") or ""),
                "permalink": link,
                "thumbnail_url": str(media.get("thumbnail_url") or ""),
                "likes": _number(media.get("like_count")),
                "comments": _number(media.get("comments_count")),
                "views": None, "views_change": None,
            })
            if len(result) >= MAX_REELS:
                return result
        next_cursor = (response.get("paging") or {}).get("cursors") or {}
        cursor = next_cursor.get("after")
        if not cursor or cursor == after:
            break
        after = str(cursor)
    return result


async def _media_views(client: httpx.AsyncClient, token: str, media_id: str) -> int | None:
    data = await _graph_get(client, f"{media_id}/insights", token, {"metric": "views"})
    for value in data.get("data") or []:
        if value.get("name") != "views":
            continue
        total = value.get("total_value")
        if isinstance(total, dict):
            count = _number(total.get("value"))
            if count is not None:
                return count
        values = value.get("values") or []
        if values:
            return _number(values[-1].get("value"))
    return None


async def collect_account(account: dict, previous: dict | None = None) -> dict[str, Any]:
    """Fetch account + up to 50 recent Reels; views if Insights permission exists."""
    slot = int(account["slot"])
    previous = previous or {}
    now = int(time.time())
    result = {
        "slot": slot, "account_id": str(account.get("id") or ""),
        "username": account.get("username") or account.get("label") or "",
        "meta_app": int(account.get("meta_app", 1 if slot <= 5 else 2)),
        "followers": None, "media_count": None, "reels": [],
        "history": list(previous.get("history") or []),
        "insights_status": "unknown", "last_updated": None, "error": None,
        "last_attempt": now,
    }
    token = account.get("access_token")
    if not token:
        result["error"] = "У аккаунта нет токена Instagram"
        return result
    if _number(account.get("expires_at")) and int(account["expires_at"]) <= now:
        result["error"] = "Срок действия токена истёк. Переподключи аккаунт."
        return result
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
            profile = await _profile(client, token)
            result["username"] = profile.get("username") or result["username"]
            result["followers"] = _number(profile.get("followers_count"))
            result["media_count"] = _number(profile.get("media_count"))
            result["reels"] = await _reels(client, token)
            older = {str(r.get("id")): r for r in previous.get("reels") or []}
            missing_permission = False
            saw_view = False
            # Run a bounded number of insight lookups to avoid API rate spikes.
            # If the first insights request fails with a permissions error,
            # the UI suggests explicit re-authorization; publishing still works.
            for reel in result["reels"][:MAX_INSIGHT_CALLS]:
                try:
                    reel["views"] = await _media_views(client, token, reel["id"])
                    saw_view = saw_view or reel["views"] is not None
                    before = _number(older.get(reel["id"], {}).get("views"))
                    if before is not None and reel["views"] is not None:
                        reel["views_change"] = reel["views"] - before
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in (400, 401, 403):
                        missing_permission = True
                        break
                    if exc.response.status_code == 429:
                        result["error"] = "Достигнут лимит запросов Insights; повторим позже"
                        break
                except httpx.HTTPError:
                    result["error"] = "Часть просмотров сейчас недоступна"
                    break
            if missing_permission:
                result["insights_status"] = "permission_required"
            else:
                result["insights_status"] = "available" if saw_view else "no_data"
            result["last_updated"] = now
    except Exception as exc:
        result["error"] = _public_error(exc)
        # Retain last good data instead of replacing with zeros on a bad request.
        for field in ("followers", "media_count", "reels", "history", "last_updated"):
            result[field] = previous.get(field, result[field])
        result["insights_status"] = previous.get("insights_status", "unknown")
        return result

    if result["followers"] is not None:
        point = {"at": now, "followers": result["followers"]}
        if not result["history"] or result["history"][-1]["followers"] != point["followers"] or now - result["history"][-1]["at"] >= 1800:
            result["history"].append(point)
        result["history"] = [p for p in result["history"] if p.get("at", 0) >= now - 90 * 86400][-3000:]
    return result


async def refresh_all() -> None:
    async with REFRESH_LOCK:
        # Read stored credentials only on the server; never return them to UI.
        accounts = await asyncio.to_thread(store._read)
        connected = accounts.get("instagram") or []
        saved = await asyncio.to_thread(_read)
        previous_accounts = saved.get("accounts") or {}
        # Keep not-yet-processed accounts visible during long refreshes.
        updated = dict(previous_accounts)
        connected_slots = {str(account["slot"]) for account in connected}
        updated = {slot: value for slot, value in updated.items() if slot in connected_slots}
        for account in connected:
            slot = int(account["slot"])
            old = previous_accounts.get(str(slot), {})
            if str(old.get("account_id")) != str(account.get("id")):
                old = {}
            try:
                item = await collect_account(account, old)
            except Exception as exc:
                logger.warning("Instagram stats refresh failed for slot %s: %s", slot, type(exc).__name__)
                item = dict(old)
                item["error"] = "Не удалось обновить статистику"
                item["slot"] = slot
                item["account_id"] = str(account.get("id") or "")
                item["last_attempt"] = int(time.time())
            updated[str(slot)] = item
            # Save account-by-account so progress is visible while others load.
            saved["accounts"] = dict(updated)
            await asyncio.to_thread(_write, saved)
        saved["accounts"] = updated
        saved["last_checked"] = int(time.time())
        await asyncio.to_thread(_write, saved)


def _local_only(request: Request) -> None:
    if not request.client or request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "Статистика доступна только локально")


@router.get("")
async def stats_status(request: Request) -> dict:
    _local_only(request)
    saved = await asyncio.to_thread(_read)
    accounts = await asyncio.to_thread(store.list_accounts)
    known = saved.get("accounts", {})
    visible = []
    for a in accounts.get("instagram", []):
        slot = str(a["slot"])
        stats = known.get(slot, {})
        if stats and str(stats.get("account_id")) != str(a.get("id")):
            stats = {}
        visible.append({
            "slot": a["slot"], "label": a.get("label"), "id": a.get("id"),
            "meta_app": a.get("meta_app", 1),
            "stats": stats or None,
        })
    return {
        "accounts": visible, "last_checked": saved.get("last_checked"),
        "refreshing": REFRESH_LOCK.locked(), "interval_minutes": REFRESH_SECONDS // 60,
        "limit_reels": MAX_REELS, "limit_view_insights": MAX_INSIGHT_CALLS,
    }


@router.post("/refresh", status_code=202)
async def stats_refresh(request: Request) -> dict:
    _local_only(request)
    global MANUAL_TASK
    if REFRESH_LOCK.locked() or (MANUAL_TASK and not MANUAL_TASK.done()):
        return {"ok": True, "refreshing": True}
    MANUAL_TASK = asyncio.create_task(refresh_all())
    return {"ok": True, "refreshing": True}


async def periodic_refresh() -> None:
    """Refresh twice per hour while the local PostingTTII process is running."""
    try:
        await asyncio.sleep(4)
        while True:
            try:
                if not REFRESH_LOCK.locked():
                    await refresh_all()
            except Exception as exc:
                logger.warning("Instagram stats background poll: %s", type(exc).__name__)
            await asyncio.sleep(REFRESH_SECONDS)
    except asyncio.CancelledError:
        raise
