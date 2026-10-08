from __future__ import annotations

import asyncio
import secrets
import shutil
import time
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile, File
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
    interval_minutes: int = 20
    interval_max_minutes: int | None = None
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


JAPANESE_REEL_ENDINGS = (
    "今日のひとコマ 🌸\n#おすすめ #動画 #日常",
    "楽しい瞬間をシェア ✨\n#おすすめ #リール #楽しい",
    "また見たくなる瞬間 🎮\n#動画 #おすすめ #エンタメ",
)


def instagram_caption(caption: str, filename: str) -> str:
    """Keep the existing caption and append a Japanese ending exactly once."""
    import hashlib
    caption = (caption or "").strip()
    if any(ending in caption for ending in JAPANESE_REEL_ENDINGS):
        return caption
    index = int(hashlib.sha256(filename.encode("utf-8")).hexdigest()[:8], 16) % len(JAPANESE_REEL_ENDINGS)
    ending = JAPANESE_REEL_ENDINGS[index]
    return f"{caption}\n\n{ending}" if caption else ending


async def _publish_single_target(video: Path, caption: str, target: str) -> dict:
    try:
        platform, slot_text = target.split(":", 1)
        slot = int(slot_text)
    except Exception as exc:
        raise HTTPException(400, "Некорректный target") from exc

    if slot not in (range(1, 6) if platform == "instagram" else (1, 2)):
        raise HTTPException(400, "Недопустимый номер аккаунта")
    if platform not in {"youtube", "instagram", "tiktok"}:
        raise HTTPException(400, f"Неизвестная платформа: {platform}")
    if platform == "tiktok" and not settings.tiktok_enabled:
        raise HTTPException(503, "TikTok временно отключен")

    if _circle_conflict(video.name, target):
        raise HTTPException(409, "Кружок уже назначен другому Instagram")
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
            result = await instagram_upload(slot, video_url, instagram_caption(caption, video.name), cover_url=cover_url)
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


async def _run_batch_job(job_id: str, body: BatchPublishRequest) -> None:
    job = BATCH_JOBS[job_id]
    job["status"] = "running"
    job["started_at"] = int(time.time())
    blocked_targets: dict[str, str] = {}

    try:
        for index, filename in enumerate(body.filenames):
            if job.get("cancel_requested"):
                job["status"] = "cancelled"
                return

            video = _safe_video(filename)
            item = job["items"][index]
            item["started_at"] = int(time.time())

            # Fast pre-check: if this exact file is already on a selected
            # account, mark it immediately. If nothing remains to publish,
            # skip AI generation and move straight to the next clip without
            # waiting the batch interval.
            video_key = publish_state.video_key(video)
            pending_targets: list[str] = []

            for target in body.targets:
                target_state = item["targets"][target]
                if target.startswith("instagram:") and job["assignments"].get(filename) != target:
                    target_state["status"] = "blocked"
                    target_state["message"] = "Кружок назначен другому Instagram"
                    continue
                if target in blocked_targets:
                    target_state["status"] = "blocked"
                    target_state["message"] = blocked_targets[target]
                elif publish_state.is_completed(video_key, target):
                    target_state["status"] = "already"
                    target_state["message"] = "Уже на аккаунте — сразу следующий"
                else:
                    pending_targets.append(target)

            if not pending_targets:
                statuses = [x["status"] for x in item["targets"].values()]
                item["status"] = (
                    "done"
                    if all(x in {"done", "already"} for x in statuses)
                    else "partial"
                )
                item["finished_at"] = int(time.time())
                job["completed_videos"] = index + 1
                continue

            caption = (body.captions.get(filename) or "").strip()
            if caption:
                item["status"] = "publishing"
                item["caption"] = caption
            else:
                item["status"] = "caption"
                try:
                    caption = await asyncio.to_thread(
                        generate_caption, video.name, body.hint
                    )
                    item["caption"] = caption
                except Exception as exc:
                    item["status"] = "error"
                    item["error"] = f"OpenAI: {exc}"
                    item["finished_at"] = int(time.time())
                    job["completed_videos"] = index + 1
                    continue

            published_now = False
            item["status"] = "publishing"
            for target in pending_targets:
                if job.get("cancel_requested"):
                    job["status"] = "cancelled"
                    return

                target_state = item["targets"][target]

                if target in blocked_targets:
                    target_state["status"] = "blocked"
                    target_state["message"] = blocked_targets[target]
                    continue

                remaining = publish_state.cooldown_remaining(
                    target, settings.post_cooldown_minutes * 60
                )
                if remaining > 0:
                    target_state["status"] = "waiting"
                    target_state["message"] = "Жду паузу аккаунта"
                    target_state["retry_after_seconds"] = remaining
                    job["status"] = "waiting_account"
                    wait_until = time.time() + remaining
                    while time.time() < wait_until:
                        if job.get("cancel_requested"):
                            job["status"] = "cancelled"
                            return
                        target_state["retry_after_seconds"] = max(
                            0, int(wait_until - time.time())
                        )
                        await asyncio.sleep(
                            min(5, max(0.2, wait_until - time.time()))
                        )
                    target_state["retry_after_seconds"] = 0
                    job["status"] = "running"

                target_state["status"] = "publishing"
                target_state["message"] = "Публикую"
                try:
                    result = await _publish_single_target(video, caption, target)
                    if result.get("skipped"):
                        target_state["status"] = "already"
                        target_state["message"] = "Уже на аккаунте"
                    elif result.get("ok"):
                        target_state["status"] = "done"
                        target_state["message"] = "Опубликовано"
                        published_now = True
                    else:
                        target_state["status"] = "error"
                        target_state["message"] = result.get("error", "Ошибка")
                except HTTPException as exc:
                    detail = exc.detail
                    if exc.status_code == 429 and isinstance(detail, dict):
                        target_state["status"] = "cooldown"
                        target_state["message"] = detail.get("message", "Пауза")
                        target_state["retry_after_seconds"] = int(
                            detail.get("retry_after_seconds", 0)
                        )
                    else:
                        target_state["status"] = "error"
                        target_state["message"] = (
                            detail if isinstance(detail, str) else str(detail)
                        )
                except Exception as exc:
                    message = str(exc)
                    target_state["status"] = "error"
                    target_state["message"] = message

                    # YouTube may temporarily block further uploads for a channel.
                    # Once detected, do not waste time retrying the same account
                    # for every remaining clip in this batch.
                    if target.startswith("youtube:") and (
                        "exceeded the number of videos they may upload" in message.lower()
                        or "uploadlimitexceeded" in message.lower()
                    ):
                        blocked_targets[target] = (
                            "Лимит загрузок YouTube — пропуск до конца этой очереди"
                        )
                        job["blocked_targets"][target] = blocked_targets[target]

            statuses = [x["status"] for x in item["targets"].values()]
            if all(x in {"done", "already"} for x in statuses):
                item["status"] = "done"
            elif any(x == "error" for x in statuses):
                item["status"] = "partial"
            else:
                item["status"] = "partial"
            item["finished_at"] = int(time.time())
            job["completed_videos"] = index + 1

            # Wait between clips only when this clip actually produced at
            # least one new publication. Already-uploaded/blocked/failed clips
            # move to the next item immediately.
            if (
                published_now
                and index < len(body.filenames) - 1
                and not job.get("cancel_requested")
            ):
                job["status"] = "waiting"
                wait_seconds = secrets.SystemRandom().randint(
                    body.interval_minutes * 60,
                    (body.interval_max_minutes or body.interval_minutes) * 60,
                )
                job["next_video_at"] = int(time.time() + wait_seconds)
                while time.time() < job["next_video_at"]:
                    if job.get("cancel_requested"):
                        job["status"] = "cancelled"
                        return
                    await asyncio.sleep(
                        min(5, max(0.2, job["next_video_at"] - time.time()))
                    )
                job["next_video_at"] = None
                job["status"] = "running"

        job["status"] = "done"
        job["finished_at"] = int(time.time())
    except Exception as exc:
        job["status"] = "error"
        job["error"] = str(exc)
        job["finished_at"] = int(time.time())
    finally:
        for circle_id in job.get("claimed_circles", []):
            IG_CIRCLE_CLAIMS.pop(circle_id, None)


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
    minimum = max(1, settings.post_cooldown_minutes)
    if body.interval_minutes < minimum:
        raise HTTPException(
            400,
            f"Интервал должен быть не меньше {minimum} минут",
        )
    maximum = body.interval_max_minutes if body.interval_max_minutes is not None else body.interval_minutes
    if maximum < body.interval_minutes or maximum > 24 * 60:
        raise HTTPException(400, "Неверный диапазон паузы")

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
    instagram_targets = [t for t in targets if t.startswith("instagram:")]
    assignments = {}
    claims = []
    for filename in filenames:
        circle_id = _circle_id(filename)
        if circle_id and instagram_targets:
            owner = _circle_owner(circle_id)
            if owner is None:
                owner = instagram_targets[len(claims) % len(instagram_targets)]
                IG_CIRCLE_CLAIMS[circle_id] = owner
                claims.append(circle_id)
            assignments[filename] = owner
    normalized = BatchPublishRequest(
        filenames=filenames,
        targets=targets,
        interval_minutes=body.interval_minutes,
        interval_max_minutes=maximum,
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
        "interval_minutes": body.interval_minutes,
        "interval_max_minutes": maximum,
        "targets": targets,
        "cancel_requested": False,
        "error": "",
        "blocked_targets": {},
        "assignments": assignments,
        "claimed_circles": claims,
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
            if slot not in (1, 2, 3, 4, 5) or (slot > 2 and platform != "instagram"):
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
                result = await instagram_upload(slot, video_url, instagram_caption(body.caption, video.name), cover_url=cover_url)
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
    return {"covers": {str(slot): (COVER_DIR / f"{slot}.jpg").exists() for slot in range(1, 6)}}


@app.post("/api/instagram/covers/{slot}")
async def upload_instagram_cover(slot: int, file: UploadFile = File(...)):
    if slot not in range(1, 6):
        raise HTTPException(400, "Номер аккаунта должен быть от 1 до 5")
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


@app.get("/instagram-covers")
async def instagram_covers_page():
    return HTMLResponse("""<!doctype html><html lang="ru"><meta charset="utf-8">
<title>Instagram — обложки</title><style>
body{font:16px system-ui;background:#11131a;color:white;max-width:760px;margin:40px auto;padding:20px}
section{background:#202431;border-radius:14px;padding:18px;margin:14px 0}
button{padding:10px 18px;border:0;border-radius:8px;background:#7d63f7;color:white;cursor:pointer}
input{margin:12px 0}img{max-height:180px;display:block;margin-top:10px}
</style><h1>Обложки Instagram Reels</h1>
<p>Назначь свою JPG/PNG-обложку каждому слоту. Видео без обложки не публикуется.</p>
<div id="items"></div><script>
const names=['Аккаунт 1','Аккаунт 2','Аккаунт 3','Аккаунт 4','Аккаунт 5'];
const root=document.getElementById('items');
for(let i=1;i<=5;i++){let el=document.createElement('section');
el.innerHTML='<h3>Instagram #'+i+' — '+names[i-1]+'</h3><input type="file" accept="image/png,image/jpeg"><button>Сохранить</button><span></span>';
let inp=el.querySelector('input'),btn=el.querySelector('button'),out=el.querySelector('span');
btn.onclick=async()=>{if(!inp.files.length)return;let form=new FormData();form.append('file',inp.files[0]);
let r=await fetch('/api/instagram/covers/'+i,{method:'POST',body:form});
out.textContent=r.ok?' ✓ Сохранено':' Ошибка: '+await r.text();};root.append(el);}
fetch('/api/instagram/covers').then(r=>r.json()).then(d=>{[...root.children].forEach((el,i)=>{
if(d.covers[String(i+1)])el.querySelector('span').textContent=' ✓ Обложка сохранена';});});
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
    if platform == "instagram" and not (settings.instagram_client_id and settings.instagram_client_secret):
        raise HTTPException(400, "Заполни INSTAGRAM_CLIENT_ID и INSTAGRAM_CLIENT_SECRET в .env")


@app.get("/connect/{platform}/{slot}")
async def connect(platform: Literal["youtube", "tiktok", "instagram"], slot: int):
    if slot not in (range(1, 6) if platform == "instagram" else (1, 2)):
        raise HTTPException(400, "Недопустимый слот")
    _require_platform(platform)
    if platform == "tiktok" and not settings.tiktok_enabled:
        raise HTTPException(503, "TikTok временно отключен")

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
    # Keep one stable local process so browser-profile sessions and in-memory jobs
    # are not interrupted by development auto-reload.
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)
