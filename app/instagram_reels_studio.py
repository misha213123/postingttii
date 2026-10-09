"""Instagram public Reels importer for Telegram Studio.

Only public profiles. Instagram may limit listings or require authenticated access.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.config import settings

router = APIRouter(prefix="/api/telegram-studio/instagram", tags=["instagram-studio"])
ROOT = settings.data_dir / "telegram_studio"
STATE = ROOT / "instagram_sources.json"
JOB = {"status": "idle", "total": 0, "done": 0, "skipped": 0, "account": "", "errors": []}
LOCK = asyncio.Lock()
TASK = None


def read_state():
    if not STATE.exists():
        return {"accounts": [], "processed": []}
    data = json.loads(STATE.read_text(encoding="utf-8"))
    data.setdefault("accounts", [])
    data.setdefault("processed", [])
    return data


def persist(data):
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE)


def username(value):
    value = value.strip()
    if value.startswith("@"):
        handle = value[1:]
    elif "://" not in value and "/" not in value:
        handle = value
    else:
        parts = urlparse(value)
        if parts.scheme != "https" or parts.hostname not in ("instagram.com", "www.instagram.com"):
            raise HTTPException(400, "Нужна ссылка https://www.instagram.com/username/")
        segments = [part for part in parts.path.split("/") if part]
        if len(segments) != 1:
            raise HTTPException(400, "Укажи ссылку на профиль, не на отдельный Reel")
        handle = segments[0]
    if not re.fullmatch(r"[A-Za-z0-9._]{1,30}", handle) or handle.lower() in ("reel", "reels", "p", "stories", "explore"):
        raise HTTPException(400, "Некорректный Instagram username")
    return handle.lower()


class AccountInput(BaseModel):
    url: str


class StartInput(BaseModel):
    limit_per_account: int = 30
    scan_limit: int = 300


@router.get("/status")
def status():
    d = read_state()
    try:
        import yt_dlp
        ready = True
    except ImportError:
        ready = False
    return {"accounts": d["accounts"], "job": JOB, "downloader_ready": ready}


@router.post("/accounts")
def add_account(body: AccountInput):
    name = username(body.url)
    d = read_state()
    if name not in d["accounts"]:
        d["accounts"].append(name)
        persist(d)
    return {"accounts": d["accounts"]}


@router.delete("/accounts/{account}")
def delete_account(account: str):
    name = username(account)
    if LOCK.locked():
        raise HTTPException(409, "Дождись завершения текущего скачивания")
    d = read_state()
    d["accounts"] = [value for value in d["accounts"] if value != name]
    persist(d)
    return {"accounts": d["accounts"]}


def collect(account, scan_limit):
    from yt_dlp import YoutubeDL
    options = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
               "playlistend": scan_limit, "ignoreerrors": True, "skip_download": True,
               "socket_timeout": 25, "retries": 2}
    with YoutubeDL(options) as ydl:
        profile = ydl.extract_info(f"https://www.instagram.com/{account}/reels/", download=False)
    entries = (profile or {}).get("entries") or []
    found = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        ident = str(entry.get("id") or "")
        link = entry.get("webpage_url") or entry.get("url") or ""
        if link and not str(link).startswith("http") and ident:
            link = f"https://www.instagram.com/reel/{ident}/"
        if not str(link).startswith("https://www.instagram.com/reel/"):
            if not ident:
                continue
            link = f"https://www.instagram.com/reel/{ident}/"
        if not ident:
            match = re.search(r"/reel/([^/?]+)", link)
            ident = match.group(1) if match else ""
        if not re.fullmatch(r"[A-Za-z0-9_-]+", ident):
            continue
        found.append({"id": ident, "url": link, "date": entry.get("timestamp") or entry.get("upload_date") or 0, "index": index})
    # Newest-first profile listings: reverse when dates are unavailable.
    if any(item["date"] for item in found):
        found.sort(key=lambda x: (str(x["date"] or "999999999999"), -x["index"]))
    else:
        found.reverse()
    return found


def download(item, account):
    from yt_dlp import YoutubeDL
    target_dir = settings.upload_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = f"ig_{account}_{item['id']}"
    options = {"quiet": True, "no_warnings": True, "noplaylist": True,
               "format": "best[ext=mp4]/best", "outtmpl": str(target_dir / (stem + ".%(ext)s")),
               "restrictfilenames": True, "socket_timeout": 30, "retries": 2,
               "overwrites": False}
    with YoutubeDL(options) as ydl:
        ydl.download([item["url"]])
    matches = list(target_dir.glob(stem + ".*"))
    return any(p.suffix.lower() == ".mp4" and p.stat().st_size > 0 for p in matches)


async def worker(limit, scan_limit):
    JOB.update(status="running", total=0, done=0, skipped=0, account="", errors=[])
    try:
        data = read_state()
        done = set(data["processed"])
        for account in list(data["accounts"]):
            JOB["account"] = account
            try:
                found = await asyncio.to_thread(collect, account, scan_limit)
                new = [item for item in found if f"{account}:{item['id']}" not in done]
                selected = new[:limit]
                JOB["skipped"] += len(found) - len(new)
                JOB["total"] += len(selected)
                for item in selected:
                    key = f"{account}:{item['id']}"
                    try:
                        ok = await asyncio.to_thread(download, item, account)
                        if not ok:
                            raise RuntimeError("MP4 не получен; формат может быть недоступен")
                        done.add(key)
                        data["processed"] = sorted(done)
                        persist(data)
                        JOB["done"] += 1
                    except Exception as exc:
                        JOB["errors"].append(f"{account}/{item['id']}: {str(exc)[:220]}")
            except Exception as exc:
                JOB["errors"].append(f"{account}: {str(exc)[:240]}")
        JOB["status"] = "done" if not JOB["errors"] else "done_with_errors"
    except Exception as exc:
        JOB["status"] = "error"
        JOB["errors"].append(str(exc)[:300])
    finally:
        JOB["account"] = ""
        LOCK.release()


@router.post("/run")
async def run(body: StartInput):
    if not 1 <= body.limit_per_account <= 500 or not 1 <= body.scan_limit <= 3000:
        raise HTTPException(400, "Лимит скачивания 1–500, глубина поиска 1–3000")
    if LOCK.locked():
        raise HTTPException(409, "Скачивание Instagram уже выполняется")
    if not read_state()["accounts"]:
        raise HTTPException(400, "Сначала добавь Instagram-аккаунт")
    try:
        import yt_dlp
    except ImportError:
        raise HTTPException(503, "Установи зависимости: pip install -r requirements.txt")
    global TASK
    await LOCK.acquire()
    TASK = asyncio.create_task(worker(body.limit_per_account, body.scan_limit))
    return {"started": True}
