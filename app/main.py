from __future__ import annotations

import asyncio
import json
import secrets
import shutil
import subprocess
import os
import time
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile, File, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import settings
from app.services.openai_text import generate_caption
from app.publish_state import publish_state
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
from app.account_creator.api.accounts import router as account_manager_router
from app.account_creator.api.aliases import router as account_aliases_router
from app.account_creator.api.profile import router as account_profile_router
from app.account_creator.api.browser_profiles import router as account_browser_router
from app.account_creator.api.tiktok import router as account_tiktok_router
from app.account_creator.api.instagram import router as account_instagram_router

from app.telegram_studio import router as telegram_studio_router, shutdown_telegram_studio

app = FastAPI(title="PostingTTII", version="0.1.0")
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(account_manager_router)
app.include_router(account_aliases_router)
app.include_router(account_profile_router)
app.include_router(account_browser_router)
app.include_router(account_tiktok_router)
app.include_router(account_instagram_router)
app.include_router(telegram_studio_router)

@app.on_event("shutdown")
async def stop_telegram_studio():
    await shutdown_telegram_studio()

@app.get("/telegram-studio", response_class=HTMLResponse)
async def telegram_studio_page():
    return HTMLResponse((STATIC_DIR / "telegram-studio.html").read_text(encoding="utf-8"))

OAUTH_STATES: dict[str, tuple[str, int]] = {}
MEDIA_TOKENS: dict[str, Path] = {}
BATCH_JOBS: dict[str, dict] = {}
IG_CIRCLE_CLAIMS: dict[str, str] = {}

def _circle_id(filename: str) -> str | None:
    import re
    m = re.fullmatch(r"tg_([a-zA-Z0-9_]+)_([0-9]+)[.]mp4", Path(filename).name, re.I)
    return f"{m.group(1).lower()}:{m.group(2)}" if m else None

def _circle_owner(circle_id: str) -> str | None:
    for item in publish_state._read().values():
        if _circle_id(item.get("filename", "")) == circle_id:
            for target in item.get("targets", {}):
                if target.startswith("instagram:"):
                    return target
    return IG_CIRCLE_CLAIMS.get(circle_id)

def _circle_conflict(filename: str, target: str) -> bool:
    circle_id = _circle_id(filename)
    return bool(target.startswith("instagram:") and circle_id and
                _circle_owner(circle_id) not in (None, target))



async def _expire_media_token(token: str, delay_seconds: int = 300) -> None:
    """Keep an Instagram source URL alive briefly after publish/error.

    Meta can continue reading the source video while finalizing a Reel.
    """
    await asyncio.sleep(delay_seconds)
    MEDIA_TOKENS.pop(token, None)


class CaptionRequest(BaseModel):
    filename: str
    hint: str = ""


class PublishRequest(BaseModel):
    filename: str
    caption: str
    targets: list[str] | None = None


class PublishTargetRequest(BaseModel):
    filename: str
    caption: str
    target: str


class BatchPublishRequest(BaseModel):
    filenames: list[str]
    targets: list[str]
    interval_seconds: int = 60
    interval_max_seconds: int = 160
    shuffle_videos: bool = True
    hint: str = ""
    captions: dict[str, str] = Field(default_factory=dict)


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
            video_key = publish_state.video_key(path)
            result.append({
                "name": path.name,
                "size_mb": round(path.stat().st_size / 1024 / 1024, 1),
                "completed_targets": publish_state.summary(video_key),
            })
    return result


@app.get("/", response_class=HTMLResponse)
async def home():
    index = Path(__file__).resolve().parent.parent / "static" / "index.html"
    return HTMLResponse(index.read_text(encoding="utf-8"))


@app.get("/api/video-preview/{filename}")
async def video_preview(filename: str):
    path = _safe_video(filename)
    media_type = {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".m4v": "video/x-m4v",
        ".webm": "video/webm",
    }.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
        content_disposition_type="inline",
    )


@app.get("/api/status")
async def status():
    return {
        "accounts": store.list_accounts(),
        "videos": _videos(),
        "upload_dir": str(settings.upload_dir),
        "post_cooldown_minutes": settings.post_cooldown_minutes,
        "cooldowns": {
            target: publish_state.cooldown_remaining(
                target, settings.post_cooldown_minutes * 60
            )
            for platform in ("youtube", "instagram")
            for account in store.list_accounts().get(platform, [])
            for target in [f"{platform}:{account['slot']}"]
        },
        "configured": {
            "openai": bool(settings.openai_api_key),
            "youtube": bool(settings.youtube_client_id and settings.youtube_client_secret),
            "tiktok": bool(settings.tiktok_client_key and settings.tiktok_client_secret),
            "tiktok_enabled": settings.tiktok_enabled,
            "instagram": bool(settings.instagram_client_id and settings.instagram_client_secret),
            "instagram_public_url": bool(settings.public_base_url),
            "instagram_second_app": bool(settings.instagram_client_id_2 and settings.instagram_client_secret_2),
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


JAPANESE_ACCOUNT_STYLES = (
    ("今日の注目シーンをチェック！🎬", "#おすすめ #リール #切り抜き #配信 #話題"),
    ("思わずもう一度見たくなる瞬間 ✨", "#動画 #おすすめ #面白い #ハイライト #リール"),
    ("このシーン、どう思う？ 👀", "#おすすめ #リアクション #エンタメ #動画 #注目"),
    ("印象に残るワンシーンをお届け 🎧", "#リール #動画 #クリップ #瞬間 #おすすめ"),
    ("今日のベストシーンはこちら！ 🎮", "#ゲーム #配信 #切り抜き #おすすめ #リール"),
)


def instagram_caption(caption: str, filename: str, slot: int = 1) -> str:
    """Japanese-only Reel caption, with a distinct stable style per Instagram account."""
    import hashlib
    import re

    intro, _ = JAPANESE_ACCOUNT_STYLES[(slot - 1) % len(JAPANESE_ACCOUNT_STYLES)]
    raw = (caption or "").strip()
    # Legacy Russian/English captions must not leak into Japanese-only Reels.
    if re.search(r"[\\u0400-\\u04ff]", raw) or re.search(r"[A-Za-z]{3,}", raw):
        raw = ""
    # Remove previous hashtag blocks; keep account-specific Japanese tags.
    body = re.sub(r"#[^\\s#]+", "", raw).strip()
    if not body:
        variants = (
            "気になるシーンをまとめました。ぜひ最後まで見てね！",
            "何度でも見たくなるワンシーン。お気に入りの瞬間を見つけよう！",
            "今回のハイライトをお届け。楽しんでもらえたら嬉しいです！",
            "注目の瞬間をシェアします。感想をコメントで教えてね！",
        )
        digest = hashlib.sha256((filename + str(slot)).encode("utf-8")).digest()
        body = variants[int.from_bytes(digest[:2], "big") % len(variants)]
    return f"{intro}\\n{body}"


async def _publish_single_target(video: Path, caption: str, target: str) -> dict:
    try:
        platform, slot_text = target.split(":", 1)
        slot = int(slot_text)
    except Exception as exc:
        raise HTTPException(400, "Некорректный target") from exc

    if slot not in (range(1, 9) if platform == "instagram" else (1, 2)):
        raise HTTPException(400, "Недопустимый номер аккаунта")
    if platform not in {"youtube", "instagram", "tiktok"}:
        raise HTTPException(400, f"Неизвестная платформа: {platform}")
    if platform == "tiktok" and not settings.tiktok_enabled:
        raise HTTPException(503, "TikTok временно отключен")

    video_key = publish_state.video_key(video)
    if publish_state.is_completed(video_key, target):
        return {
            "target": target,
            "ok": True,
            "skipped": True,
            "message": "Уже опубликовано на этом аккаунте.",
        }

    remaining = publish_state.cooldown_remaining(
        target, settings.post_cooldown_minutes * 60
    )
    if remaining > 0:
        raise HTTPException(
            status_code=429,
            detail={
                "message": "Для этого аккаунта действует пауза между публикациями.",
                "retry_after_seconds": remaining,
                "target": target,
            },
        )

    media_token = secrets.token_urlsafe(24)
    MEDIA_TOKENS[media_token] = video
    video_url = (
        f"{settings.public_base_url}/media/{media_token}"
        if settings.public_base_url
        else ""
    )

    try:
        if platform == "youtube":
            result = await youtube_upload(slot, video, caption)
        elif platform == "instagram":
            cover_path = COVER_DIR / f"{slot}.jpg"
            if not cover_path.exists():
                raise HTTPException(400, f"Instagram #{slot}: сначала загрузи обложку на /instagram-covers")
            cover_token = secrets.token_urlsafe(24)
            MEDIA_TOKENS[cover_token] = cover_path
            cover_url = f"{settings.public_base_url}/media/{cover_token}" if settings.public_base_url else ""
            result = await instagram_upload(slot, video_url, instagram_caption(caption, video.name, slot), cover_url=cover_url)
        else:
            result = await tiktok_upload(slot, video, caption)

        publish_state.mark_completed(
            video_key=video_key,
            filename=video.name,
            target=target,
            result=result,
        )
        return {"target": target, "ok": True, "result": result}
    finally:
        if platform == "instagram":
            # Do not cut Meta off immediately: keep the source URL alive for
            # another five minutes after success/error.
            asyncio.create_task(_expire_media_token(media_token, 300))
        else:
            MEDIA_TOKENS.pop(media_token, None)


BATCH_STATE_DIR = settings.data_dir / "batch_jobs"
BATCH_STATE_DIR.mkdir(parents=True, exist_ok=True)


def _save_batch_job(job: dict) -> None:
    path = BATCH_STATE_DIR / (job["id"] + ".json")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


@app.on_event("startup")
async def restore_batch_jobs():
    for path in BATCH_STATE_DIR.glob("*.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
            BATCH_JOBS[job["id"]] = job
            if job["status"] not in {"done", "cancelled", "error"}:
                job["status"] = "queued"
                body = BatchPublishRequest(**job["request"])
                asyncio.create_task(_run_batch_job(job["id"], body))
        except Exception:
            continue


async def _run_batch_job(job_id: str, body: BatchPublishRequest) -> None:
    """Publish each wave to all selected accounts concurrently, respecting cooldowns."""
    job = BATCH_JOBS[job_id]
    job["status"] = "running"
    job["started_at"] = job.get("started_at") or int(time.time())
    try:
        for index, filename in enumerate(body.filenames):
            if job.get("cancel_requested"):
                job["status"] = "cancelled"
                return
            video = _safe_video(filename)
            item = job["items"][index]
            key = publish_state.video_key(video)
            pending = []
            for target in body.targets:
                state = item["targets"][target]
                if state["status"] in {"done", "already"} or publish_state.is_completed(key, target):
                    state.update(status="already", message="Уже опубликовано")
                else:
                    pending.append(target)
            if not pending:
                item["status"] = "done"
                job["completed_videos"] = index + 1
                _save_batch_job(job)
                continue
            caption = (body.captions.get(filename) or item.get("caption") or "").strip()
            if not caption:
                item["status"] = "caption"
                _save_batch_job(job)
                try:
                    caption = await asyncio.to_thread(generate_caption, video.name, body.hint)
                except Exception as exc:
                    item.update(status="error", error=f"Описание: {exc}", finished_at=int(time.time()))
                    job["completed_videos"] = index + 1
                    _save_batch_job(job)
                    continue
            item["caption"] = caption

            async def publish_one(target: str) -> None:
                state = item["targets"][target]
                while not job.get("cancel_requested"):
                    if publish_state.is_completed(key, target):
                        state.update(status="already", message="Уже опубликовано")
                        return
                    remaining = publish_state.cooldown_remaining(
                        target, settings.post_cooldown_minutes * 60
                    )
                    if remaining > 0:
                        state.update(status="cooldown", message="Ожидаю аккаунт",
                                     retry_after_seconds=int(remaining))
                        _save_batch_job(job)
                        await asyncio.sleep(min(5, max(0.2, remaining)))
                        continue
                    state.update(status="publishing", message="Публикую")
                    _save_batch_job(job)
                    try:
                        result = await _publish_single_target(video, caption, target)
                        state.update(status="already" if result.get("skipped") else "done",
                                     message="Уже было" if result.get("skipped") else "Опубликовано")
                        return
                    except HTTPException as exc:
                        if exc.status_code == 429:
                            detail = exc.detail if isinstance(exc.detail, dict) else {}
                            remaining = int(detail.get("retry_after_seconds", 60))
                            state.update(status="cooldown", message="Лимит API, жду",
                                         retry_after_seconds=remaining)
                            _save_batch_job(job)
                            await asyncio.sleep(min(60, max(1, remaining)))
                            continue
                        state.update(status="error", message=str(exc.detail))
                        return
                    except Exception as exc:
                        state.update(status="error", message=str(exc))
                        return

            item["status"] = "publishing"
            _save_batch_job(job)
            await asyncio.gather(*(publish_one(t) for t in pending))
            if job.get("cancel_requested"):
                job["status"] = "cancelled"
                return
            statuses = [t["status"] for t in item["targets"].values()]
            item["status"] = "done" if all(t in {"done", "already"} for t in statuses) else "partial"
            item["finished_at"] = int(time.time())
            job["completed_videos"] = index + 1
            _save_batch_job(job)
            if index < len(body.filenames) - 1:
                wait = secrets.SystemRandom().randint(body.interval_seconds, body.interval_max_seconds)
                job["next_video_at"] = int(time.time() + wait)
                job["status"] = "waiting"
                _save_batch_job(job)
                while time.time() < job["next_video_at"]:
                    if job.get("cancel_requested"):
                        job["status"] = "cancelled"
                        return
                    await asyncio.sleep(min(3, max(0.1, job["next_video_at"] - time.time())))
                job["next_video_at"] = None
                job["status"] = "running"
        job["status"] = "done"
    except Exception as exc:
        job["status"] = "error"
        job["error"] = str(exc)
    finally:
        job["finished_at"] = int(time.time())
        _save_batch_job(job)


@app.post("/api/batch/start")
async def batch_start(body: BatchPublishRequest):
    filenames = list(dict.fromkeys(Path(x).name for x in body.filenames))
    if body.shuffle_videos:
        secrets.SystemRandom().shuffle(filenames)
    targets = list(dict.fromkeys(body.targets))

    if not filenames:
        raise HTTPException(400, "Выбери хотя бы одно видео")
    if not targets:
        raise HTTPException(400, "Выбери хотя бы один аккаунт")
    if not 60 <= body.interval_seconds <= body.interval_max_seconds <= 86400:
        raise HTTPException(400, "Пауза должна быть от 60 секунд")

    for filename in filenames:
        _safe_video(filename)

    allowed_targets = {
        f"{platform}:{account['slot']}"
        for platform in ("youtube", "instagram")
        for account in store.list_accounts().get(platform, [])
    }
    invalid = [target for target in targets if target not in allowed_targets]
    if invalid:
        raise HTTPException(
            400,
            "Недоступные аккаунты: " + ", ".join(invalid),
        )

    job_id = secrets.token_urlsafe(12)
    assignments = {filename: list(targets) for filename in filenames}
    normalized = BatchPublishRequest(
        filenames=filenames,
        targets=targets,
        interval_seconds=body.interval_seconds,
        interval_max_seconds=body.interval_max_seconds,
        shuffle_videos=body.shuffle_videos,
        hint=body.hint,
        captions={
            filename: (body.captions.get(filename) or "").strip()
            for filename in filenames
            if (body.captions.get(filename) or "").strip()
        },
    )
    BATCH_JOBS[job_id] = {
        "id": job_id,
        "status": "queued",
        "created_at": int(time.time()),
        "started_at": None,
        "finished_at": None,
        "next_video_at": None,
        "completed_videos": 0,
        "total_videos": len(filenames),
        "interval_seconds": body.interval_seconds,
        "interval_max_seconds": body.interval_max_seconds,
        "targets": targets,
        "cancel_requested": False,
        "error": "",
        "blocked_targets": {},
        "assignments": assignments,
        "claimed_circles": [],
        "items": [
            {
                "filename": filename,
                "status": "queued",
                "caption": normalized.captions.get(filename, ""),
                "error": "",
                "targets": {
                    target: {"status": "queued", "message": ""}
                    for target in targets
                },
            }
            for filename in filenames
        ],
    }

    BATCH_JOBS[job_id]["request"] = normalized.model_dump()
    _save_batch_job(BATCH_JOBS[job_id])
    asyncio.create_task(_run_batch_job(job_id, normalized))
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/batch/{job_id}")
async def batch_status(job_id: str):
    job = BATCH_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Очередь не найдена")
    return job


@app.post("/api/batch/{job_id}/cancel")
async def batch_cancel(job_id: str):
    job = BATCH_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Очередь не найдена")
    if job["status"] in {"done", "cancelled", "error"}:
        return job
    job["cancel_requested"] = True
    return {"ok": True}


@app.post("/api/publish-target")
async def publish_target(body: PublishTargetRequest):
    video = _safe_video(body.filename)
    try:
        return await _publish_single_target(video, body.caption, body.target)
    except HTTPException:
        raise
    except Exception as exc:
        return {
            "target": body.target,
            "ok": False,
            "error": str(exc),
        }


@app.post("/api/publish")
async def publish(body: PublishRequest):
    video = _safe_video(body.filename)
    accounts = store.list_accounts()

    if body.targets:
        targets = body.targets
    else:
        targets = []
        default_platforms = ["youtube", "instagram"]
        if settings.tiktok_enabled:
            default_platforms.append("tiktok")
        for platform in default_platforms:
            for account in accounts.get(platform, []):
                targets.append(f"{platform}:{account['slot']}")

    if not targets:
        raise HTTPException(400, "Нет подключенных аккаунтов")

    video_key = publish_state.video_key(video)

    media_token = secrets.token_urlsafe(24)
    MEDIA_TOKENS[media_token] = video
    video_url = f"{settings.public_base_url}/media/{media_token}" if settings.public_base_url else ""

    results = []
    failures = 0

    for target in targets:
        # Never publish the same exact file twice to the same connected account.
        if publish_state.is_completed(video_key, target):
            results.append({
                "target": target,
                "ok": True,
                "skipped": True,
                "message": "Уже опубликовано ранее — пропущено, чтобы не было дубля.",
            })
            continue

        try:
            platform, slot_text = target.split(":", 1)
            slot = int(slot_text)
            if slot not in range(1, 9) or (slot > 2 and platform != "instagram"):
                raise RuntimeError("Недопустимый слот аккаунта")

            if platform == "youtube":
                result = await youtube_upload(slot, video, body.caption)
            elif platform == "tiktok":
                if not settings.tiktok_enabled:
                    raise RuntimeError("TikTok временно отключен")
                result = await tiktok_upload(slot, video, body.caption)
            elif platform == "instagram":
                cover_path = COVER_DIR / f"{slot}.jpg"
                if not cover_path.is_file():
                    raise RuntimeError("Сначала выбери обложку для Instagram")
                cover_token = secrets.token_urlsafe(24)
                MEDIA_TOKENS[cover_token] = cover_path
                cover_url = f"{settings.public_base_url}/media/{cover_token}"
                result = await instagram_upload(slot, video_url, instagram_caption(body.caption, video.name, slot), cover_url=cover_url)
                asyncio.create_task(_expire_media_token(cover_token, 300))
            else:
                raise RuntimeError(f"Неизвестная платформа: {platform}")

            publish_state.mark_completed(
                video_key=video_key,
                filename=video.name,
                target=target,
                result=result,
            )
            results.append({"target": target, "ok": True, "result": result})
        except Exception as exc:
            failures += 1
            results.append({"target": target, "ok": False, "error": str(exc)})

    # Move to posted only when every target requested in this run is now completed.
    all_done = all(publish_state.is_completed(video_key, target) for target in targets)
    if all_done:
        destination = settings.posted_dir / video.name
        if destination.exists():
            destination = settings.posted_dir / f"{video.stem}_{secrets.token_hex(3)}{video.suffix}"
        shutil.move(str(video), str(destination))

    MEDIA_TOKENS.pop(media_token, None)
    return {
        "ok": failures == 0,
        "all_done": all_done,
        "results": results,
    }



COVER_DIR = settings.data_dir / "instagram_covers"
COVER_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/api/instagram/covers")
async def list_instagram_covers():
    return {"covers": {str(slot): (COVER_DIR / f"{slot}.jpg").exists() for slot in range(1, 9)}}


@app.post("/api/instagram/covers/{slot}")
async def upload_instagram_cover(slot: int, file: UploadFile = File(...)):
    if slot not in range(1, 9):
        raise HTTPException(400, "Номер аккаунта должен быть от 1 до 8")
    data = await file.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(413, "Обложка больше 8 МБ")
    from io import BytesIO
    from PIL import Image, UnidentifiedImageError
    try:
        image = Image.open(BytesIO(data))
        image.verify()
        image = Image.open(BytesIO(data)).convert("RGB")
        if image.width < 320 or image.height < 320:
            raise ValueError("Слишком маленькая обложка")
    except (UnidentifiedImageError, ValueError, OSError) as exc:
        raise HTTPException(400, f"Неверный JPG/PNG: {exc}") from exc
    image.save(COVER_DIR / f"{slot}.jpg", "JPEG", quality=92)
    return {"slot": slot, "saved": True}


@app.get("/api/instagram/covers/{slot}/preview")
async def instagram_cover_preview(slot: int):
    if slot not in range(1, 9):
        raise HTTPException(400, "Недопустимый слот")
    path = COVER_DIR / f"{slot}.jpg"
    if not path.is_file():
        raise HTTPException(404, "Обложка не выбрана")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/instagram-covers")
async def instagram_covers_page():
    return HTMLResponse("""<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Instagram — обложки</title><style>
*{box-sizing:border-box}body{font:15px system-ui;background:#10131b;color:#f4f5ff;max-width:1100px;margin:0 auto;padding:28px 18px}
header{display:flex;align-items:center;justify-content:space-between;gap:15px;flex-wrap:wrap;margin-bottom:24px}
h1{font-size:26px;margin:0}a{color:#b9abff;text-decoration:none}.muted{color:#a4a8b8}
#items{display:grid;grid-template-columns:repeat(auto-fill,minmax(225px,1fr));gap:16px}
section{background:#1d2230;border:1px solid #353b4b;border-radius:16px;padding:16px}
section h3{margin:0 0 12px;font-size:16px}
.preview{height:260px;background:#121722;border-radius:12px;display:flex;align-items:center;justify-content:center;overflow:hidden;color:#a4a8b8;margin-bottom:14px}
.preview img{width:100%;height:100%;object-fit:cover}input{width:100%;font-size:12px;margin-bottom:12px}
button{padding:10px 14px;border:0;border-radius:9px;background:#7965e8;color:white;cursor:pointer;width:100%}
.status{display:block;min-height:22px;margin-top:10px;font-size:12px}
</style><header><div><h1>Обложки Instagram Reels</h1>
<p class="muted">Текущие обложки и загрузка новых для 8 аккаунтов</p></div><a href="/">← Назад к публикациям</a></header>
<div id="items"></div><script>
const root=document.getElementById('items');
async function load(){
  const r=await fetch('/api/instagram/covers');const d=await r.json();
  root.replaceChildren();
  for(let i=1;i<=8;i++){
    const el=document.createElement('section');
    const h=document.createElement('h3');h.textContent='Instagram #'+i;el.append(h);
    const preview=document.createElement('div');preview.className='preview';el.append(preview);
    function show(hasCover){
      preview.replaceChildren();
      if(hasCover){const img=document.createElement('img');img.alt='Обложка аккаунта '+i;img.src='/api/instagram/covers/'+i+'/preview?t='+Date.now();preview.append(img);}
      else preview.textContent='Обложка не выбрана';
    }
    show(Boolean(d.covers[String(i)]));
    const inp=document.createElement('input');inp.type='file';inp.accept='image/png,image/jpeg';el.append(inp);
    const btn=document.createElement('button');btn.textContent='Сохранить обложку';el.append(btn);
    const status=document.createElement('span');status.className='status';el.append(status);
    btn.onclick=async()=>{
      if(!inp.files.length){status.textContent='Выбери JPG или PNG';return;}
      btn.disabled=true;status.textContent='Загрузка...';
      try{const form=new FormData();form.append('file',inp.files[0]);
        const response=await fetch('/api/instagram/covers/'+i,{method:'POST',body:form});
        if(!response.ok)throw new Error(await response.text());
        show(true);status.textContent='✓ Обложка сохранена';inp.value='';
      }catch(err){status.textContent='Ошибка: '+err.message;}
      finally{btn.disabled=false;}
    };
    root.append(el);
  }
}
load().catch(err=>{root.textContent='Не удалось загрузить обложки: '+err.message;});
</script></html>""")


@app.get("/media/{token}")
async def public_media(token: str):
    path = MEDIA_TOKENS.get(token)
    if not path or not path.exists():
        raise HTTPException(404, "Media token expired")
    return FileResponse(path, media_type="image/jpeg" if path.suffix.lower() == ".jpg" else "video/mp4", filename=path.name)


def _require_platform(platform: str) -> None:
    if platform == "youtube" and not (settings.youtube_client_id and settings.youtube_client_secret):
        raise HTTPException(400, "Заполни YOUTUBE_CLIENT_ID и YOUTUBE_CLIENT_SECRET в .env")
    if platform == "tiktok" and not (settings.tiktok_client_key and settings.tiktok_client_secret):
        raise HTTPException(400, "Заполни TIKTOK_CLIENT_KEY и TIKTOK_CLIENT_SECRET в .env")



def _edge_executable() -> str:
    candidates = [
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\\Program Files (x86)")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\\Program Files")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    executable = shutil.which("msedge")
    if executable:
        return executable
    raise HTTPException(503, "Microsoft Edge не найден на этом компьютере")


@app.post("/api/instagram/edge/{slot}")
def open_instagram_edge(slot: int, request: Request, mode: Literal["connect", "profile"] = "connect"):
    # Launching local applications must never be possible through a public tunnel.
    if not request.client or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "Открывать Edge можно только из локального приложения")
    if slot not in (6, 7, 8):
        raise HTTPException(400, "Edge-профили доступны для слотов 6–8")
    if mode == "connect":
        if not (settings.instagram_client_id_2 and settings.instagram_client_secret_2):
            raise HTTPException(400, "Настрой INSTAGRAM_CLIENT_ID_2 и INSTAGRAM_CLIENT_SECRET_2")
        url = f"http://127.0.0.1:{settings.port}/connect/instagram/{slot}"
    else:
        account = store.get("instagram", slot)
        if not account:
            raise HTTPException(404, "Сначала подключи Instagram-аккаунт")
        username = account.get("username", "").strip().lstrip("@")
        url = f"https://www.instagram.com/{username}/" if username else "https://www.instagram.com/"
    profile_dir = (settings.data_dir / "edge_instagram_profiles" / f"slot_{slot}").resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.Popen(
            [_edge_executable(), f"--user-data-dir={profile_dir}", "--no-first-run", url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise HTTPException(503, f"Не удалось запустить Microsoft Edge: {exc}") from exc
    return {"ok": True, "slot": slot, "mode": mode}


@app.get("/connect/{platform}/{slot}")
async def connect(platform: Literal["youtube", "tiktok", "instagram"], slot: int):
    if slot not in (range(1, 9) if platform == "instagram" else (1, 2)):
        raise HTTPException(400, "Недопустимый слот")
    _require_platform(platform)
    if platform == "instagram":
        if slot <= 5 and not (settings.instagram_client_id and settings.instagram_client_secret):
            raise HTTPException(400, "Заполни INSTAGRAM_CLIENT_ID и INSTAGRAM_CLIENT_SECRET в .env")
        if slot >= 6 and not (settings.instagram_client_id_2 and settings.instagram_client_secret_2):
            raise HTTPException(400, "Заполни INSTAGRAM_CLIENT_ID_2 и INSTAGRAM_CLIENT_SECRET_2 в .env")
    if platform == "tiktok" and not settings.tiktok_enabled:
        raise HTTPException(503, "TikTok временно отключен")

    state = secrets.token_urlsafe(32)
    OAUTH_STATES[state] = (platform, slot)
    if platform == "youtube":
        url = youtube_auth_url(state)
    elif platform == "tiktok":
        url = tiktok_auth_url(state)
    else:
        url = instagram_auth_url(state, slot)
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
        account = await instagram_exchange(code, slot)
        store.save("instagram", slot, account)
    except Exception as exc:
        raise HTTPException(500, f"Instagram OAuth: {exc}") from exc
    return RedirectResponse("/?connected=instagram")


@app.delete("/api/account/{platform}/{slot}")
async def disconnect(platform: Literal["youtube", "tiktok", "instagram"], slot: int):
    if slot not in (range(1, 9) if platform == "instagram" else (1, 2)):
        raise HTTPException(400, "Недопустимый слот")
    store.delete(platform, slot)
    return {"ok": True}


if __name__ == "__main__":
    # Keep one stable local process so browser-profile sessions and in-memory jobs
    # are not interrupted by development auto-reload.
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)
