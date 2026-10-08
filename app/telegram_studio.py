"""Isolated Telegram Studio API: local Telegram media -> random backgrounds -> Reels."""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from app.config import settings

router = APIRouter(prefix="/api/telegram-studio", tags=["telegram-studio"])
ROOT = settings.data_dir / "telegram_studio"
BACKGROUNDS = ROOT / "backgrounds"
ORIGINALS = ROOT / "originals"
CONFIG = ROOT / "config.json"
SESSION = ROOT / "telegram"
for p in (ROOT, BACKGROUNDS, ORIGINALS):
    p.mkdir(parents=True, exist_ok=True)
LOCK = asyncio.Lock()
JOB = {"status": "idle", "total": 0, "done": 0, "errors": []}


def config():
    if not CONFIG.exists():
        return {"channels": [], "processed": [], "backgrounds_disabled": []}
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def save(data):
    temp = CONFIG.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(CONFIG)


def normalize(value):
    value = value.strip().rstrip("/")
    value = re.sub(r"^https?://(?:www[.])?(?:t[.]me|telegram[.]me)/", "", value, flags=re.I)
    value = value.lstrip("@")
    value = value.split("/")[0]
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", value):
        raise HTTPException(400, "Укажи публичный @username или ссылку t.me/username")
    return value.lower()


def backgrounds():
    data = config()
    return [{"name": p.name, "enabled": p.name not in data["backgrounds_disabled"],
             "size_mb": round(p.stat().st_size / 1048576, 1)}
            for p in sorted(BACKGROUNDS.glob("*.mp4"))]


class ChannelInput(BaseModel):
    channel: str


class ToggleInput(BaseModel):
    enabled: bool


class LoginStart(BaseModel):
    phone: str


class LoginFinish(BaseModel):
    code: str
    password: str = ""


class RenderRequest(BaseModel):
    limit_per_channel: int = 30


@router.get("/status")
def status():
    d = config()
    return {"channels": d["channels"], "backgrounds": backgrounds(), "job": JOB,
            "telegram_configured": bool(os.getenv("TG_API_ID") and os.getenv("TG_API_HASH")),
            "telegram_session_exists": SESSION.with_suffix(".session").exists(),
            "ffmpeg_ready": bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))}


@router.post("/channels")
def add_channel(body: ChannelInput):
    name = normalize(body.channel)
    d = config()
    if name not in d["channels"]:
        d["channels"].append(name)
        save(d)
    return {"channels": d["channels"]}


@router.delete("/channels/{channel}")
def remove_channel(channel: str):
    d = config()
    d["channels"] = [x for x in d["channels"] if x != normalize(channel)]
    save(d)
    return {"channels": d["channels"]}


@router.post("/backgrounds")
async def upload_background(file: UploadFile = File(...)):
    if not file.filename or Path(file.filename).suffix.lower() != ".mp4":
        raise HTTPException(400, "Поддерживаются только MP4")
    name = uuid.uuid4().hex[:10] + ".mp4"
    dest = BACKGROUNDS / name
    size = 0
    try:
        with dest.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > 1024 * 1024 * 1024:
                    raise HTTPException(413, "Максимум 1 ГБ на фон")
                out.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    return {"name": name, "backgrounds": backgrounds()}


@router.delete("/backgrounds/{name}")
def remove_background(name: str):
    if Path(name).name != name:
        raise HTTPException(400, "Некорректный файл")
    (BACKGROUNDS / name).unlink(missing_ok=True)
    d = config()
    d["backgrounds_disabled"] = [x for x in d["backgrounds_disabled"] if x != name]
    save(d)
    return {"backgrounds": backgrounds()}


@router.post("/backgrounds/{name}/toggle")
def toggle_background(name: str, body: ToggleInput):
    if not (BACKGROUNDS / name).is_file():
        raise HTTPException(404, "Фон не найден")
    d = config()
    disabled = set(d["backgrounds_disabled"])
    if body.enabled:
        disabled.discard(name)
    else:
        disabled.add(name)
    d["backgrounds_disabled"] = sorted(disabled)
    save(d)
    return {"backgrounds": backgrounds()}


@router.get("/backgrounds/{name}/preview")
def background_preview(name: str):
    if Path(name).name != name or not (BACKGROUNDS / name).is_file():
        raise HTTPException(404, "Фон не найден")
    return FileResponse(BACKGROUNDS / name, media_type="video/mp4")


def telegram_client():
    try:
        from telethon import TelegramClient
    except ImportError:
        raise HTTPException(503, "Установи telethon из requirements.txt")
    api_id, api_hash = os.getenv("TG_API_ID"), os.getenv("TG_API_HASH")
    if not api_id or not api_hash:
        raise HTTPException(400, "Заполни TG_API_ID и TG_API_HASH в .env")
    return TelegramClient(str(SESSION), int(api_id), api_hash)


@router.get("/auth")
async def auth_status():
    async with telegram_client() as client:
        return {"authorized": await client.is_user_authorized()}


@router.post("/auth/start")
async def auth_start(body: LoginStart):
    async with telegram_client() as client:
        result = await client.send_code_request(body.phone)
        d = config()
        d["auth_phone"] = body.phone
        d["phone_code_hash"] = result.phone_code_hash
        save(d)
    return {"ok": True}


@router.post("/auth/finish")
async def auth_finish(body: LoginFinish):
    from telethon.errors import SessionPasswordNeededError
    d = config()
    if not d.get("auth_phone") or not d.get("phone_code_hash"):
        raise HTTPException(400, "Сначала запроси код")
    async with telegram_client() as client:
        try:
            await client.sign_in(phone=d["auth_phone"], code=body.code, phone_code_hash=d["phone_code_hash"])
        except SessionPasswordNeededError:
            if not body.password:
                return {"password_required": True}
            await client.sign_in(password=body.password)
    d.pop("auth_phone", None)
    d.pop("phone_code_hash", None)
    save(d)
    return {"authorized": True}


def duration(path):
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                       capture_output=True, text=True, check=True)
    return float(p.stdout.strip())


def render(source: Path, bg: Path, target: Path):
    seconds = duration(source)
    bg_seconds = duration(bg)
    if seconds <= 0 or bg_seconds <= 0:
        raise ValueError("Некорректная длительность")
    offset = random.uniform(0, max(0, bg_seconds - seconds))
    # Circular Telegram video messages are already square with a circular visible area.
    # Overlay square with transparent corners, preserving the original audio.
    filt = ("[0:v]scale=760:760:force_original_aspect_ratio=increase,"
            "crop=760:760,format=rgba,"
            "geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
            "a='if(lte((X-380)*(X-380)+(Y-380)*(Y-380),144400),255,0)'[circle];"
            "[1:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,setsar=1[bg];"
            "[bg][circle]overlay=(W-w)/2:(H-h)/2:shortest=1,"
            "format=yuv420p[v]")
    cmd = ["ffmpeg", "-y", "-i", str(source), "-stream_loop", "-1",
           "-ss", str(offset), "-i", str(bg), "-filter_complex", filt,
           "-map", "[v]", "-map", "0:a?", "-t", str(seconds),
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
           "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(target)]
    subprocess.run(cmd, capture_output=True, text=True, check=True)


async def process(limit):
    JOB.update(status="running", total=0, done=0, errors=[])
    try:
        d = config()
        bgs = [BACKGROUNDS / b["name"] for b in backgrounds() if b["enabled"]]
        if not bgs:
            raise ValueError("Добавь хотя бы один активный фон")
        async with telegram_client() as client:
            if not await client.is_user_authorized():
                raise ValueError("Сначала авторизуй Telegram")
            for channel in d["channels"]:
                try:
                    entity = await client.get_entity(channel)
                    async for message in client.iter_messages(entity, limit=limit):
                        if not message.video_note:
                            continue
                        key = f"{channel}:{message.id}"
                        if key in d["processed"]:
                            continue
                        JOB["total"] += 1
                        source = ORIGINALS / f"{channel}_{message.id}.mp4"
                        target = settings.upload_dir / f"tg_{channel}_{message.id}.mp4"
                        try:
                            if not source.exists():
                                await client.download_media(message, file=str(source))
                            if not source.exists():
                                raise ValueError("Не удалось скачать кружок")
                            await asyncio.to_thread(render, source, random.choice(bgs), target)
                            d["processed"].append(key)
                            save(d)
                            JOB["done"] += 1
                        except Exception as exc:
                            target.unlink(missing_ok=True)
                            JOB["errors"].append(f"{key}: {str(exc)[:250]}")
                except Exception as exc:
                    JOB["errors"].append(f"{channel}: {str(exc)[:250]}")
        JOB["status"] = "done"
    except Exception as exc:
        JOB["status"] = "error"
        JOB["errors"].append(str(exc)[:300])
    finally:
        LOCK.release()


@router.post("/run")
async def run(body: RenderRequest):
    if LOCK.locked():
        raise HTTPException(409, "Обработка уже запущена")
    if not 1 <= body.limit_per_channel <= 500:
        raise HTTPException(400, "Лимит 1–500")
    await LOCK.acquire()
    asyncio.create_task(process(body.limit_per_channel))
    return {"started": True}
