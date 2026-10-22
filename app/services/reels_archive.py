"""Reels archive eligibility monitor.

Meta supports reading Reel insights but does not expose an Archive Media action.
This module *never* changes remote Instagram media. It creates a local,
persistent action queue of eligible Reels for review in the Instagram UI.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.services.platforms import _instagram_access_token
from app.store import store

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/reels-archive", tags=["reels-archive"])

THRESHOLD_VIEWS = 2500
MIN_AGE_SECONDS = 24 * 60 * 60
CHECK_EVERY_SECONDS = 60 * 60
PAGE_LIMIT = 3  # Up to 150 most recent posts per account per scan.
PAGE_SIZE = 50


def _timestamp(value: str) -> int | None:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return None
        return int(dt.timestamp())
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None


def _views(payload: dict[str, Any]) -> int | None:
    """Do not interpret missing, malformed, or denied metrics as zero."""
    for entry in payload.get("data", []):
        if not isinstance(entry, dict) or entry.get("name") != "views":
            continue
        total = entry.get("total_value")
        value = total.get("value") if isinstance(total, dict) else None
        if value is None:
            values = entry.get("values")
            if isinstance(values, list) and values:
                latest = values[-1]
                value = latest.get("value") if isinstance(latest, dict) else None
        if type(value) is int and value >= 0:
            return value
        if type(value) is float and value >= 0 and value.is_integer():
            return int(value)
    return None


def _safe_api_error(response: httpx.Response) -> str:
    try:
        error = response.json().get("error", {})
        code = error.get("code", response.status_code) if isinstance(error, dict) else response.status_code
    except (ValueError, AttributeError, TypeError):
        code = response.status_code
    # Never save the request URL, credentials, or Graph's untrusted error text.
    return f"Meta API HTTP {response.status_code} (code {code})"


class LocalAction(BaseModel):
    account_id: str
    media_id: str


class ReelsArchiveMonitor:
    def __init__(self, path: Path):
        self.path = path
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    def _read(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
        except (OSError, ValueError):
            pass
        return {"records": {}, "accounts": {}, "last_scan_at": None}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, self.path)

    def status(self) -> dict[str, Any]:
        data = self._read()
        connected = {
            str(a.get("id")) for a in store.list_accounts().get("instagram", [])
        }
        items = [v for v in data.get("records", {}).values()
                 if isinstance(v, dict) and str(v.get("account_id")) in connected]
        items.sort(key=lambda r: r.get("published_at") or 0, reverse=True)
        pending = [r for r in items if r.get("status") == "needs_manual_archive"]
        return {
            "threshold_views": THRESHOLD_VIEWS,
            "minimum_age_hours": MIN_AGE_SECONDS // 3600,
            "check_interval_minutes": CHECK_EVERY_SECONDS // 60,
            "max_media_per_account": PAGE_LIMIT * PAGE_SIZE,
            "automatic_check_enabled": self._task is not None and not self._task.done(),
            "automatic_archive_available": False,
            "reason": "Meta Instagram API не поддерживает отправку Reels в архив.",
            "running": self._lock.locked(),
            "last_scan_at": data.get("last_scan_at"),
            "accounts": data.get("accounts", {}),
            "pending_count": len(pending),
            "pending": pending[:200],
            "recent": items[:200],
        }

    async def _scan_account(self, client: httpx.AsyncClient, account: dict, records: dict, now: int) -> dict:
        slot = int(account["slot"])
        token, full = await _instagram_access_token(slot)
        account_id = str(full["id"])
        base = f"https://graph.instagram.com/{settings.instagram_graph_version}"
        after: str | None = None
        seen_cursors: set[str] = set()
        summary = {"label": account.get("label", ""), "slot": slot,
                   "checked": 0, "pending": 0, "errors": 0, "scanned_media": 0,
                   "last_checked_at": now, "error": None}
        for _ in range(PAGE_LIMIT):
            params = {
                "fields": "id,media_type,media_product_type,timestamp,permalink",
                "limit": PAGE_SIZE,
                "access_token": token,
            }
            if after:
                params["after"] = after
            page = await client.get(f"{base}/{account_id}/media", params=params)
            if page.is_error:
                raise RuntimeError(_safe_api_error(page))
            payload = page.json()
            media_list = payload.get("data", [])
            if not isinstance(media_list, list):
                raise RuntimeError("Meta API: неверный формат списка публикаций")
            summary["scanned_media"] += len(media_list)
            for media in media_list:
                if not isinstance(media, dict):
                    continue
                if media.get("media_product_type") != "REELS":
                    continue
                media_id = str(media.get("id") or "")
                published_at = _timestamp(media.get("timestamp", ""))
                if not media_id or published_at is None:
                    continue
                key = f"{account_id}:{media_id}"
                existing = records.get(key, {})
                if existing.get("status") in {"marked_archived", "dismissed"}:
                    continue

                record = {
                    "account_id": account_id, "account": account.get("label", ""),
                    "slot": slot, "media_id": media_id,
                    "permalink": media.get("permalink", ""),
                    "published_at": published_at,
                    "eligible_at": published_at + MIN_AGE_SECONDS,
                    "checked_at": now, "views": existing.get("views"),
                    "status": "waiting_24h", "error": None,
                }
                if now < record["eligible_at"]:
                    records[key] = record
                    continue
                summary["checked"] += 1
                try:
                    insights = await client.get(
                        f"{base}/{media_id}/insights",
                        params={"metric": "views", "access_token": token},
                    )
                    if insights.is_error:
                        raise RuntimeError(_safe_api_error(insights))
                    views = _views(insights.json())
                    if views is None:
                        raise RuntimeError("Метрика views не предоставлена Meta")
                    record["views"] = views
                    record["status"] = (
                        "needs_manual_archive" if views < THRESHOLD_VIEWS else "retained"
                    )
                    if record["status"] == "needs_manual_archive":
                        summary["pending"] += 1
                except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                    summary["errors"] += 1
                    record["status"] = "metrics_unavailable"
                    record["error"] = str(exc)[:160]
                records[key] = record
            paging = payload.get("paging") or {}
            cursors = paging.get("cursors") or {}
            new_after = cursors.get("after") if paging.get("next") else None
            if not new_after or new_after in seen_cursors or not media_list:
                break
            seen_cursors.add(new_after)
            after = new_after
        return summary

    async def scan(self) -> dict[str, Any]:
        if self._lock.locked():
            raise HTTPException(409, "Проверка Reels уже выполняется")
        async with self._lock:
            data = self._read()
            records = data.setdefault("records", {})
            summaries = {}
            now = int(time.time())
            async with httpx.AsyncClient(timeout=25) as client:
                for account in store.list_accounts().get("instagram", []):
                    account_id = str(account.get("id", ""))
                    try:
                        summary = await self._scan_account(client, account, records, now)
                    except (httpx.HTTPError, ValueError, KeyError, RuntimeError) as exc:
                        summary = {
                            "label": account.get("label", ""),
                            "slot": account.get("slot"),
                            "error": str(exc)[:160],
                            "last_checked_at": now,
                            "checked": 0, "pending": 0, "errors": 1,
                            "scanned_media": 0,
                        }
                    summaries[account_id] = summary
            data["last_scan_at"] = now
            data["accounts"] = summaries
            self._write(data)
        return self.status()

    async def mark_local(self, action: LocalAction, status: str) -> dict:
        """Records user confirmation only; does not archive on Instagram."""
        if status not in {"marked_archived", "dismissed"}:
            raise HTTPException(400, "Неверное действие")
        async with self._lock:
            data = self._read()
            key = f"{action.account_id}:{action.media_id}"
            record = data.get("records", {}).get(key)
            if not record or record.get("status") not in {"needs_manual_archive", "metrics_unavailable"}:
                raise HTTPException(404, "Публикация отсутствует в очереди")
            record["status"] = status
            record["local_action_at"] = int(time.time())
            self._write(data)
        return self.status()

    async def run_loop(self):
        while True:
            try:
                await self.scan()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Reels archive monitor scan failed")
            await asyncio.sleep(CHECK_EVERY_SECONDS)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run_loop(), name="reels-archive-monitor")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None


monitor = ReelsArchiveMonitor(settings.data_dir / "reels_archive_state.json")


@router.get("/status")
async def archive_status():
    return monitor.status()


@router.post("/check")
async def archive_check():
    return await monitor.scan()


@router.post("/mark-archived")
async def archive_mark_archived(body: LocalAction):
    return await monitor.mark_local(body, "marked_archived")


@router.post("/dismiss")
async def archive_dismiss(body: LocalAction):
    return await monitor.mark_local(body, "dismissed")
