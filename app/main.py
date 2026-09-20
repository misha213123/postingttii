from __future__ import annotations

import secrets
import shutil
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel

from app.config import settings
from app.services.openai_text import generate_caption
from app.services.platforms import (
    instagram_auth_url,
    instagram_exchange,
    instagram_upload,
    tiktok_auth_url,
    tiktok_exchange,
    tiktok_upload,
    youtube_auth_url,
    youtube_exchange,
    youtube_upload,
)
from app.store import store

app = FastAPI(title="PostingTTII", version="0.1.0")

OAUTH_STATES: dict[str, tuple[str, int]] = {}
MEDIA_TOKENS: dict[str, Path] = {}


class CaptionRequest(BaseModel):
    filename: str
    hint: str = ""


class PublishRequest(BaseModel):
    filename: str
    caption: str
    targets: list[str] | None = None


def _safe_video(filename: str) -> Path:
    name = Path(filename).name
    path = (settings.upload_dir / name).resolve()
    if path.parent != settings.upload_dir:
        raise HTTPException(400, "Некорректное имя файла")
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "Видео не найдено")
    if path.suffix.lower() not in {".mp4", ".mov", ".m4v", ".webm"}:
        raise HTTPException(400, "Формат видео не поддерживается")
    return path


def _videos() -> list[dict]:
    result = []
    for path in sorted(settings.upload_dir.iterdir(), key=lambda p: p.stat().st_mtime if p.is_file() else 0):
        if path.is_file() and path.suffix.lower() in {".mp4", ".mov", ".m4v", ".webm"}:
            result.append({"name": path.name, "size_mb": round(path.stat().st_size / 1024 / 1024, 1)})
    return result


@app.get("/", response_class=HTMLResponse)
async def home():
    index = Path(__file__).resolve().parent.parent / "static" / "index.html"
    return HTMLResponse(index.read_text(encoding="utf-8"))


@app.get("/api/status")
async def status():
    return {
        "accounts": store.list_accounts(),
        "videos": _videos(),
        "upload_dir": str(settings.upload_dir),
        "configured": {
            "openai": bool(settings.openai_api_key),
            "youtube": bool(settings.youtube_client_id and settings.youtube_client_secret),
            "tiktok": bool(settings.tiktok_client_key and settings.tiktok_client_secret),
            "instagram": bool(settings.instagram_client_id and settings.instagram_client_secret),
            "instagram_public_url": bool(settings.public_base_url),
        },
    }


@app.post("/api/caption")
async def caption(body: CaptionRequest):
    _safe_video(body.filename)
    try:
        text = generate_caption(body.filename, body.hint)
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    return {"caption": text}


@app.post("/api/publish")
async def publish(body: PublishRequest):
    video = _safe_video(body.filename)
    accounts = store.list_accounts()

    if body.targets:
        targets = body.targets
    else:
        targets = []
        for platform in ("youtube", "tiktok", "instagram"):
            for account in accounts.get(platform, []):
                targets.append(f"{platform}:{account['slot']}")

    if not targets:
        raise HTTPException(400, "Нет подключенных аккаунтов")

    media_token = secrets.token_urlsafe(24)
    MEDIA_TOKENS[media_token] = video
    video_url = f"{settings.public_base_url}/media/{media_token}" if settings.public_base_url else ""

    results = []
    failures = 0

    for target in targets:
        try:
            platform, slot_text = target.split(":", 1)
            slot = int(slot_text)
            if slot not in (1, 2):
                raise RuntimeError("Разрешены только слоты 1 и 2")

            if platform == "youtube":
                result = await youtube_upload(slot, video, body.caption)
            elif platform == "tiktok":
                result = await tiktok_upload(slot, video, body.caption)
            elif platform == "instagram":
                result = await instagram_upload(slot, video_url, body.caption)
            else:
                raise RuntimeError(f"Неизвестная платформа: {platform}")

            results.append({"target": target, "ok": True, "result": result})
        except Exception as exc:
            failures += 1
            results.append({"target": target, "ok": False, "error": str(exc)})

    if failures == 0:
        destination = settings.posted_dir / video.name
        if destination.exists():
            destination = settings.posted_dir / f"{video.stem}_{secrets.token_hex(3)}{video.suffix}"
        shutil.move(str(video), str(destination))

    MEDIA_TOKENS.pop(media_token, None)
    return {"ok": failures == 0, "results": results}


@app.get("/media/{token}")
async def public_media(token: str):
    path = MEDIA_TOKENS.get(token)
    if not path or not path.exists():
        raise HTTPException(404, "Media token expired")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


def _require_platform(platform: str) -> None:
    if platform == "youtube" and not (settings.youtube_client_id and settings.youtube_client_secret):
        raise HTTPException(400, "Заполни YOUTUBE_CLIENT_ID и YOUTUBE_CLIENT_SECRET в .env")
    if platform == "tiktok" and not (settings.tiktok_client_key and settings.tiktok_client_secret):
        raise HTTPException(400, "Заполни TIKTOK_CLIENT_KEY и TIKTOK_CLIENT_SECRET в .env")
    if platform == "instagram" and not (settings.instagram_client_id and settings.instagram_client_secret):
        raise HTTPException(400, "Заполни INSTAGRAM_CLIENT_ID и INSTAGRAM_CLIENT_SECRET в .env")


@app.get("/connect/{platform}/{slot}")
async def connect(platform: Literal["youtube", "tiktok", "instagram"], slot: int):
    if slot not in (1, 2):
        raise HTTPException(400, "Слот должен быть 1 или 2")
    _require_platform(platform)

    state = secrets.token_urlsafe(32)
    OAUTH_STATES[state] = (platform, slot)
    if platform == "youtube":
        url = youtube_auth_url(state)
    elif platform == "tiktok":
        url = tiktok_auth_url(state)
    else:
        url = instagram_auth_url(state)
    return RedirectResponse(url)


def _consume_state(state: str, expected_platform: str) -> int:
    saved = OAUTH_STATES.pop(state, None)
    if not saved or saved[0] != expected_platform:
        raise HTTPException(400, "OAuth state устарел. Нажми подключить аккаунт еще раз.")
    return saved[1]


@app.get("/auth/youtube/callback")
async def youtube_callback(code: str, state: str):
    slot = _consume_state(state, "youtube")
    try:
        account = await youtube_exchange(code)
        store.save("youtube", slot, account)
    except Exception as exc:
        raise HTTPException(500, f"YouTube OAuth: {exc}") from exc
    return RedirectResponse("/?connected=youtube")


@app.get("/auth/tiktok/callback")
async def tiktok_callback(code: str, state: str):
    slot = _consume_state(state, "tiktok")
    try:
        account = await tiktok_exchange(code, state)
        store.save("tiktok", slot, account)
    except Exception as exc:
        raise HTTPException(500, f"TikTok OAuth: {exc}") from exc
    return RedirectResponse("/?connected=tiktok")


@app.get("/auth/instagram/callback")
async def instagram_callback(code: str, state: str):
    slot = _consume_state(state, "instagram")
    try:
        account = await instagram_exchange(code)
        store.save("instagram", slot, account)
    except Exception as exc:
        raise HTTPException(500, f"Instagram OAuth: {exc}") from exc
    return RedirectResponse("/?connected=instagram")


@app.delete("/api/account/{platform}/{slot}")
async def disconnect(platform: Literal["youtube", "tiktok", "instagram"], slot: int):
    if slot not in (1, 2):
        raise HTTPException(400, "Слот должен быть 1 или 2")
    store.delete(platform, slot)
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)
