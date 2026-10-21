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
from app.services.instagram_caption_templates import (
    CaptionTemplatesError,
    instagram_caption_from_file,
    load_instagram_caption_templates,
)
from app.services.tiktok_text import generate_tiktok_caption
from app.services.youtube_text import generate_youtube_metadata
from app.services import autotok_bridge
from app.services.autotok_routes import router as autotok_router
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
from app.instagram_studio import router as instagram_reels_router
from app.instagram_stats import router as instagram_stats_router, periodic_refresh
from app.video_folders import router as video_folders_router, folder_path, list_videos, video_path, account_folder

app = FastAPI(title="PostingTTII", version="0.1.0")
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(account_manager_router)
app.include_router(account_aliases_router)
app.include_router(account_profile_router)
app.include_router(account_browser_router)
app.include_router(account_tiktok_router)
app.include_router(account_instagram_router)
app.include_router(autotok_router)
app.include_router(telegram_studio_router)
app.include_router(instagram_reels_router)
app.include_router(instagram_stats_router)
app.include_router(video_folders_router)

INSTAGRAM_STATS_TASK: asyncio.Task | None = None

@app.on_event("startup")
async def start_instagram_stats():
    global INSTAGRAM_STATS_TASK
    INSTAGRAM_STATS_TASK = asyncio.create_task(periodic_refresh())

@app.on_event("shutdown")
async def stop_instagram_stats():
    if INSTAGRAM_STATS_TASK:
        INSTAGRAM_STATS_TASK.cancel()
        try:
            await INSTAGRAM_STATS_TASK
        except asyncio.CancelledError:
            pass


@app.on_event("shutdown")
async def stop_telegram_studio():
    await shutdown_telegram_studio()

@app.get("/telegram-studio", response_class=HTMLResponse)
async def telegram_studio_page():
    return HTMLResponse((STATIC_DIR / "telegram-studio.html").read_text(encoding="utf-8"))

@app.get("/instagram-stats", response_class=HTMLResponse)
async def instagram_stats_page():
    return HTMLResponse((STATIC_DIR / "instagram-stats.html").read_text(encoding="utf-8"))


OAUTH_STATES: dict[str, tuple[str, int]] = {}
OAUTH_INSTAGRAM_META_APPS: dict[str, int] = {}
OAUTH_INSTAGRAM_INSIGHTS: dict[str, bool] = {}
META2_SLOT_LOCK = asyncio.Lock()
MEDIA_TOKENS: dict[str, Path] = {}
BATCH_JOBS: dict[str, dict] = {}
IG_CIRCLE_CLAIMS: dict[str, str] = {}

# A single Quick Tunnel video fetch can take close to a minute. Previously all
# eight Instagram Reel containers could fetch MP4 files simultaneously while
# YouTube and TikTok were uploading too. Limit Meta video processing to one
# account at a time to avoid saturating the public tunnel. Other platforms
# continue normally. This does not bypass Meta's content validation.
INSTAGRAM_META_SEMAPHORE = asyncio.Semaphore(1)

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
    source_folder: str = "default"


class PublishTargetRequest(BaseModel):
    filename: str
    caption: str
    target: str
    source_folder: str = "default"


class BatchPublishRequest(BaseModel):
    filenames: list[str]
    targets: list[str]
    source_folder: str = "default"  # "linked" uses each account's assigned folder
    interval_seconds: int = 60
    interval_max_seconds: int = 160
    wave_min_minutes: int = 22
    wave_max_minutes: int = 28
    shuffle_videos: bool = True
    hint: str = ""
    captions: dict[str, str] = Field(default_factory=dict)


def _safe_video(filename: str, folder_id: str = "default") -> Path:
    try:
        return video_path(folder_id, filename)
    except ValueError as exc:
        status_code = 404 if "не найдено" in str(exc).lower() else 400
        raise HTTPException(status_code, str(exc)) from exc


def _videos(history: dict | None = None, folder_id: str = "default") -> list[dict]:
    history = publish_state._read() if history is None else history
    result = []
    paths = (list_videos(folder_id) if folder_id != "default" else
             sorted((p for p in settings.upload_dir.iterdir()
                     if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".m4v", ".webm"}),
                    key=lambda p: p.stat().st_mtime))
    for path in paths:
        try:
            digest = publish_state.video_key(path)
            size = path.stat().st_size
        except FileNotFoundError:
            continue
        result.append({
            "name": path.name, "size_mb": round(size / 1024 / 1024, 1),
            "completed_targets": sorted(history.get(digest, {}).get("targets", {}).keys()),
        })
    return result


@app.get("/", response_class=HTMLResponse)
async def home():
    index = Path(__file__).resolve().parent.parent / "static" / "index.html"
    return HTMLResponse(index.read_text(encoding="utf-8"))


@app.get("/api/video-preview/{filename}")
async def video_preview(filename: str, folder_id: str = "default"):
    path = _safe_video(filename, folder_id)
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
async def status(folder_id: str = "default"):
    # One snapshot of each local JSON store per request. SHA-256 hashing runs
    # off the async event loop so the API stays responsive during a cold scan.
    accounts = await asyncio.to_thread(store.list_accounts)
    history = await asyncio.to_thread(publish_state._read)
    try:
        videos = await asyncio.to_thread(_videos, history, folder_id)
        active_folder = (settings.upload_dir.resolve() if folder_id == "default"
                         else folder_path(folder_id))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    latest_by_target = {}
    for item in history.values():
        for target, details in item.get("targets", {}).items():
            timestamp = int(details.get("completed_at", 0))
            latest_by_target[target] = max(latest_by_target.get(target, 0), timestamp)
    now = int(time.time())
    cooldowns = {
        target: max(0, settings.post_cooldown_minutes * 60 -
                    (now - latest_by_target.get(target, 0)))
                if latest_by_target.get(target, 0) else 0
        for platform in ("youtube", "instagram", "tiktok")
        for account in accounts.get(platform, [])
        for target in [f"{platform}:{account['slot']}"]
    }
    return {
        "accounts": accounts,
        "videos": videos,
        "upload_dir": str(active_folder),
        "folder_id": folder_id,
        "post_cooldown_minutes": settings.post_cooldown_minutes,
        "cooldowns": cooldowns,
        "configured": {
            "openai": bool(settings.openai_api_key),
            "youtube": bool(settings.youtube_client_id and settings.youtube_client_secret),
            "tiktok": bool(settings.tiktok_client_key and settings.tiktok_client_secret) or bool(autotok_bridge.executable()),
            "autotok": bool(autotok_bridge.executable()),
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
        text = instagram_caption_from_file(body.filename)
    except CaptionTemplatesError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"caption": text}


def instagram_caption(caption: str, filename: str, slot: int = 1) -> str:
    """Select only a user-provided TXT template for Instagram.

    Legacy AI captions and built-in Japanese phrases are intentionally ignored.
    """
    return instagram_caption_from_file(filename, slot)


async def _publish_single_target(
    video: Path, caption: str, target: str, *,
    cooldown_seconds: int | None = None,
    youtube_metadata: dict[str, str] | None = None,
) -> dict:
    try:
        platform, slot_text = target.split(":", 1)
        slot = int(slot_text)
    except Exception as exc:
        raise HTTPException(400, "Некорректный target") from exc

    if (platform == "instagram" and (slot <= 0 or not store.get("instagram", slot))) or (platform != "instagram" and slot not in (1, 2)):
        raise HTTPException(400, "Аккаунт не подключён или номер недопустим")
    if platform not in {"youtube", "instagram", "tiktok"}:
        raise HTTPException(400, f"Неизвестная платформа: {platform}")
    if platform == "tiktok" and not settings.tiktok_enabled and (store.get("tiktok", slot) or {}).get("provider") != "autotok":
        raise HTTPException(503, "TikTok API отключён. Подключи TikTok через AutoTok")

    video_key = publish_state.video_key(video)
    if publish_state.is_completed(video_key, target):
        return {
            "target": target,
            "ok": True,
            "skipped": True,
            "message": "Уже опубликовано на этом аккаунте.",
        }

    # Manual/single publications keep the normal account cooldown.
    # The second clip in a scheduled pair uses the configured short gap.
    effective_cooldown = (
        settings.post_cooldown_minutes * 60
        if cooldown_seconds is None else max(60, cooldown_seconds)
    )
    remaining = publish_state.cooldown_remaining(target, effective_cooldown)
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
    cover_token: str | None = None
    MEDIA_TOKENS[media_token] = video
    video_url = (
        f"{settings.public_base_url}/media/{media_token}"
        if settings.public_base_url
        else ""
    )

    try:
        if platform == "youtube":
            metadata = youtube_metadata or await asyncio.to_thread(
                generate_youtube_metadata, video.name, caption
            )
            result = await youtube_upload(
                slot, video, metadata["description"], title=metadata["title"]
            )
        elif platform == "instagram":
            # Throttle only Instagram, keeping TikTok/YouTube posting independent.
            # Keep the media URLs registered while the Meta container is being
            # created and processed; do not start a second Meta fetch meanwhile.
            async with INSTAGRAM_META_SEMAPHORE:
                cover_path = COVER_DIR / f"{slot}.jpg"
                if not cover_path.exists():
                    raise HTTPException(400, f"Instagram #{slot}: сначала загрузи обложку на /instagram-covers")
                cover_token = secrets.token_urlsafe(24)
                MEDIA_TOKENS[cover_token] = cover_path
                cover_url = f"{settings.public_base_url}/media/{cover_token}" if settings.public_base_url else ""
                result = await instagram_upload(
                    slot, video_url, instagram_caption(caption, video.name, slot),
                    cover_url=cover_url,
                )
        else:
            account = store.get("tiktok", slot) or {}
            result = (
                await autotok_bridge.publish(slot, video, caption)
                if account.get("provider") == "autotok"
                else await tiktok_upload(slot, video, caption)
            )

        publish_state.mark_completed(
            video_key=video_key,
            filename=video.name,
            target=target,
            result=result,
        )
        return {"target": target, "ok": True, "result": result}
    finally:
        if platform == "instagram":
            # Meta may still fetch both the video and its cover after a
            # container is processed. Expire both tokens after five minutes.
            asyncio.create_task(_expire_media_token(media_token, 300))
            if cover_token is not None:
                asyncio.create_task(_expire_media_token(cover_token, 300))
        else:
            MEDIA_TOKENS.pop(media_token, None)


BATCH_STATE_DIR = settings.data_dir / "batch_jobs"
BATCH_STATE_DIR.mkdir(parents=True, exist_ok=True)


def _save_batch_job(job: dict) -> None:
    path = BATCH_STATE_DIR / (job["id"] + ".json")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def archive_dir(folder_id: str = "default") -> Path:
    if folder_id == "default":
        directory = settings.posted_dir
    else:
        directory = folder_path(folder_id) / "posted"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


# Instagram continues requesting the media URL briefly after publication.
# Remove confirmed videos from inbox only when that window has elapsed.
ARCHIVE_GRACE_SECONDS = 330


async def _archive_after_publish(job_id: str, entry: dict) -> None:
    job = BATCH_JOBS[job_id]
    filename = entry["filename"]
    try:
        await asyncio.sleep(max(0, entry["due_at"] - time.time()))
        if Path(filename).name != filename:
            raise ValueError("Некорректное имя видео для архивации")
        folder_id = entry.get("source_folder", "default")
        path = folder_path(folder_id) / filename
        if not path.exists():
            job["cleanup_pending"] = [
                pending for pending in job.get("cleanup_pending", [])
                if pending is not entry
            ]
            return
        path = video_path(folder_id, filename)
        if not publish_state.completed_targets(entry["video_key"]):
            raise ValueError("Нет подтверждения публикации — исходник сохранён")
        # The previous file at the same path might have been replaced.
        if path.exists():
            actual_key = await asyncio.to_thread(publish_state.video_key, path)
            if actual_key != entry["video_key"]:
                raise ValueError("Файл изменился после публикации — исходник сохранён")
            for _ in range(20):
                if not any(p == path for p in MEDIA_TOKENS.values()):
                    break
                await asyncio.sleep(30)
            else:
                raise ValueError("Источник ещё используется API — исходник сохранён")
            destination_dir = archive_dir(folder_id)
            destination = destination_dir / filename
            if destination.exists():
                destination = destination_dir / (
                    path.stem + "_" + secrets.token_hex(4) + path.suffix
                )
            await asyncio.to_thread(shutil.move, str(path), str(destination))
            job["archived_count"] = job.get("archived_count", 0) + 1
        job["cleanup_pending"] = [
            pending for pending in job.get("cleanup_pending", [])
            if pending is not entry
        ]
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        entry["error"] = str(exc)[:300]
        job.setdefault("cleanup_errors", []).append(
            f"{filename}: {str(exc)[:250]}"
        )
    finally:
        _save_batch_job(job)


def _queue_archive(job: dict, video: Path, video_key: str, folder_id: str = "default") -> None:
    if any(p["filename"] == video.name and p.get("source_folder", "default") == folder_id
           for p in job.get("cleanup_pending", [])):
        return
    due_at = int(time.time()) + ARCHIVE_GRACE_SECONDS
    entry = {"filename": video.name, "source_folder": folder_id,
             "video_key": video_key, "due_at": due_at}
    job.setdefault("cleanup_pending", []).append(entry)
    _save_batch_job(job)
    asyncio.create_task(_archive_after_publish(job["id"], entry))


@app.on_event("startup")
async def restore_batch_jobs():
    for path in BATCH_STATE_DIR.glob("*.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
            BATCH_JOBS[job["id"]] = job
            for entry in job.get("cleanup_pending", []):
                asyncio.create_task(_archive_after_publish(job["id"], entry))
            if job["status"] in {"done", "cancelled", "error"}:
                continue
            # A stop request must survive an application restart.
            if job.get("cancel_requested"):
                job["status"] = "cancelled"
                job["finished_at"] = int(time.time())
                _save_batch_job(job)
                continue
            if job.get("assignment_mode") != "one_video_one_account":
                # An older job sent each file to every account. Do not replay it.
                job["status"] = "cancelled"
                job["error"] = (
                    "Остановлена очередь старого формата (один ролик на все аккаунты). "
                    "Создай новую очередь с уникальными роликами."
                )
                job["finished_at"] = int(time.time())
                _save_batch_job(job)
                continue
            job["status"] = "queued"
            body = BatchPublishRequest(**job["request"])
            asyncio.create_task(_run_batch_job(job["id"], body))
        except Exception:
            continue


async def _run_batch_job(job_id: str, body: BatchPublishRequest) -> None:
    """One distinct video per account; two rounds form a wave."""
    job = BATCH_JOBS[job_id]
    if job.get("assignment_mode") != "one_video_one_account":
        job["status"] = "cancelled"
        job["error"] = "Очередь старого формата. Создай новую."
        _save_batch_job(job)
        return
    job["status"] = "running"
    job["started_at"] = job.get("started_at") or int(time.time())
    per_round = len(body.targets)

    async def wait_until(timestamp: float) -> bool:
        job["status"] = "waiting"
        job["next_video_at"] = int(timestamp)
        _save_batch_job(job)
        while time.time() < timestamp:
            if job.get("cancel_requested"):
                job["status"] = "cancelled"
                return False
            await asyncio.sleep(min(3, max(0.1, timestamp - time.time())))
        job["next_video_at"] = None
        job["status"] = "running"
        return True

    async def publish_item(item: dict, second_in_pair: bool) -> None:
        if item.get("status") in {"done", "already", "error"}:
            return
        target = next(iter(item["targets"]))
        state = item["targets"][target]
        try:
            folder_id = item.get("source_folder", "default")
            video = _safe_video(item["filename"], folder_id)
            key = await asyncio.to_thread(publish_state.video_key, video)
            published_targets = publish_state.completed_targets(key)
            if published_targets:
                state.update(
                    status="already",
                    message=("Уже опубликовано: " + ", ".join(published_targets)),
                )
                item["status"] = "already"
                _queue_archive(job, video, key, folder_id)
                return
            youtube_metadata = None
            caption = (body.captions.get(video.name) or item.get("caption") or "").strip()
            if target.startswith("youtube:"):
                youtube_metadata = item.get("youtube_metadata")
                if not youtube_metadata:
                    item["status"] = "caption"
                    _save_batch_job(job)
                    youtube_metadata = await asyncio.to_thread(
                        generate_youtube_metadata, video.name,
                        body.hint or caption,
                    )
                    # Save before uploading so a restarted worker keeps the
                    # previously generated title and description.
                    item["youtube_metadata"] = youtube_metadata
                    _save_batch_job(job)
                caption = youtube_metadata["description"]
                item["youtube_title"] = youtube_metadata["title"]
            elif target.startswith("instagram:"):
                # One source of truth: the user's local TXT templates.
                slot = int(target.split(":", 1)[1])
                caption = instagram_caption_from_file(video.name, slot)
            elif not caption:
                item["status"] = "caption"
                _save_batch_job(job)
                caption = await asyncio.to_thread(generate_tiktok_caption, video.name, body.hint)
            item["caption"] = caption
            cooldown = (
                max(60, body.interval_seconds)
                if second_in_pair else settings.post_cooldown_minutes * 60
            )
            while not job.get("cancel_requested"):
                remaining = publish_state.cooldown_remaining(target, cooldown)
                if remaining > 0:
                    state.update(
                        status="cooldown", message="Ожидаю аккаунт",
                        retry_after_seconds=int(remaining),
                    )
                    _save_batch_job(job)
                    await asyncio.sleep(min(5, max(0.2, remaining)))
                    continue
                state.update(status="publishing", message="Публикую")
                _save_batch_job(job)
                try:
                    result = await _publish_single_target(
                        video, caption, target, cooldown_seconds=cooldown,
                        youtube_metadata=youtube_metadata,
                    )
                except HTTPException as exc:
                    if exc.status_code != 429:
                        raise
                    detail = exc.detail if isinstance(exc.detail, dict) else {}
                    remaining = int(detail.get("retry_after_seconds", 60))
                    state.update(
                        status="cooldown", message="Лимит API, жду",
                        retry_after_seconds=remaining,
                    )
                    _save_batch_job(job)
                    await asyncio.sleep(min(60, max(1, remaining)))
                    continue
                state.update(
                    status="already" if result.get("skipped") else "done",
                    message="Уже было" if result.get("skipped") else "Опубликовано",
                )
                item["status"] = state["status"]
                _queue_archive(job, video, key, folder_id)
                return
            state.update(status="cancelled", message="Очередь остановлена")
            item["status"] = "cancelled"
        except Exception as exc:
            state.update(status="error", message=str(exc)[:450])
            item["status"] = "error"
            item["error"] = str(exc)[:450]
        finally:
            item["finished_at"] = int(time.time())
            _save_batch_job(job)

    try:
        rounds = [
            job["items"][i:i + per_round]
            for i in range(0, len(job["items"]), per_round)
        ]
        for round_index, group in enumerate(rounds):
            if job.get("cancel_requested"):
                job["status"] = "cancelled"
                return
            # If the app restarted while waiting, respect the saved deadline.
            if job.get("next_video_at") and not await wait_until(job["next_video_at"]):
                return
            if all(item.get("status") in {"done", "already", "error"} for item in group):
                continue
            job["status"] = "running"
            await asyncio.gather(*(
                publish_item(item, second_in_pair=(round_index % 2 == 1))
                for item in group
            ))
            job["completed_videos"] = sum(
                item.get("status") in {"done", "already", "error"}
                for item in job["items"]
            )
            _save_batch_job(job)
            if job.get("cancel_requested"):
                job["status"] = "cancelled"
                return
            if round_index + 1 < len(rounds):
                after_pair = (round_index % 2 == 1)
                delay = (
                    secrets.SystemRandom().randint(
                        body.wave_min_minutes * 60, body.wave_max_minutes * 60
                    )
                    if after_pair else secrets.SystemRandom().randint(
                        body.interval_seconds, body.interval_max_seconds
                    )
                )
                job["wait_kind"] = "between_waves" if after_pair else "within_wave"
                if not await wait_until(time.time() + delay):
                    return
        job["status"] = "done"
    except Exception as exc:
        job["status"] = "error"
        job["error"] = str(exc)[:400]
    finally:
        if job["status"] in {"done", "cancelled", "error"}:
            job["finished_at"] = int(time.time())
        _save_batch_job(job)


def _active_batch_jobs() -> list[dict]:
    """Find server-side jobs even when their id is missing from browser storage."""
    return [
        job for job in BATCH_JOBS.values()
        if job.get("status") in {"queued", "running", "waiting"}
    ]


@app.get("/api/batch/active")
async def batch_active():
    jobs = _active_batch_jobs()
    return {
        "job_id": jobs[0]["id"] if jobs else None,
        "jobs": [
            {"id": job["id"], "status": job.get("status"),
             "completed_videos": job.get("completed_videos", 0),
             "total_videos": job.get("total_videos", 0)}
            for job in jobs
        ],
    }


@app.post("/api/batch/start")
async def batch_start(body: BatchPublishRequest):
    if _active_batch_jobs():
        raise HTTPException(409, "Сначала останови или заверши текущую очередь")
    targets = list(dict.fromkeys(body.targets))
    if not targets:
        raise HTTPException(400, "Выбери хотя бы один аккаунт")
    if any(target.startswith("instagram:") for target in targets):
        try:
            load_instagram_caption_templates()
        except CaptionTemplatesError as exc:
            raise HTTPException(400, str(exc)) from exc
    if not 60 <= body.interval_seconds <= body.interval_max_seconds <= 86400:
        raise HTTPException(400, "Пауза должна быть от 60 секунд")
    if not 1 <= body.wave_min_minutes <= body.wave_max_minutes <= 1440:
        raise HTTPException(400, "Некорректная пауза между волнами")
    allowed_targets = {
        f"{platform}:{account['slot']}"
        for platform in ("youtube", "instagram", "tiktok")
        for account in store.list_accounts().get(platform, [])
    }
    invalid = [target for target in targets if target not in allowed_targets]
    if invalid:
        raise HTTPException(400, "Недоступные аккаунты: " + ", ".join(invalid))

    duplicate_filenames = []
    rows: list[dict] = []
    seen_video_keys: set[str] = set()
    if body.source_folder == "linked":
        if any(not t.startswith("instagram:") for t in targets):
            raise HTTPException(400, "Привязанные папки работают только для Instagram. Сними галочки YouTube/TikTok.")
        # Every account draws independently from its own registered folder.
        # A deterministic snapshot of folder assignments is saved with job items.
        per_target: dict[str, list[Path]] = {}
        per_target_folder: dict[str, str] = {}
        for target in targets:
            try:
                slot = int(target.split(":", 1)[1])
                selected_folder = account_folder(slot)
                per_target_folder[target] = selected_folder
                videos = list_videos(selected_folder)
            except (ValueError, OSError) as exc:
                raise HTTPException(400, str(exc)) from exc
            if body.shuffle_videos:
                secrets.SystemRandom().shuffle(videos)
            per_target[target] = videos
        max_rounds = max((len(pool) for pool in per_target.values()), default=0)
        cursors = {target: 0 for target in targets}
        # Max 500 scheduled files per account to avoid runaway batch creation.
        # Advance each account's cursor until a truly unique video is found,
        # so two accounts sharing one folder still get distinct files.
        for _ in range(min(max_rounds, 500)):
            any_new = False
            for target in targets:
                pool = per_target[target]
                while cursors[target] < len(pool):
                    video = pool[cursors[target]]
                    cursors[target] += 1
                    digest = await asyncio.to_thread(publish_state.video_key, video)
                    if digest in seen_video_keys:
                        duplicate_filenames.append(video.name)
                        continue
                    seen_video_keys.add(digest)
                    rows.append({"filename": video.name, "source_folder": per_target_folder[target],
                                 "target": target, "video_key": digest})
                    any_new = True
                    break
            if not any_new:
                break
        # Require at least one distinct video per selected Instagram account.
        assigned = {row["target"] for row in rows}
        missing = [target for target in targets if target not in assigned]
        if missing:
            raise HTTPException(400, "В привязанных папках нет уникальных видео для: " + ", ".join(missing))
    else:
        try:
            folder_path(body.source_folder)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        filenames = list(dict.fromkeys(body.filenames))
        if body.shuffle_videos:
            secrets.SystemRandom().shuffle(filenames)
        if not filenames:
            raise HTTPException(400, "Выбери хотя бы одно видео")
        for name in filenames:
            video = _safe_video(name, body.source_folder)
            digest = await asyncio.to_thread(publish_state.video_key, video)
            if digest in seen_video_keys:
                duplicate_filenames.append(name)
                continue
            seen_video_keys.add(digest)
            rows.append({"filename": name, "source_folder": body.source_folder,
                         "target": targets[len(rows) % len(targets)], "video_key": digest})
        if len(rows) < len(targets):
            raise HTTPException(
                400, "Меньше уникальных видео, чем аккаунтов. Добавь видео или сними лишние галочки."
            )

    for target in targets:
        if target.startswith("tiktok:"):
            account = store.get("tiktok", int(target.split(":", 1)[1])) or {}
            if account.get("provider") == "autotok":
                try:
                    await autotok_bridge.check_account(account["autotok_account"])
                except RuntimeError as exc:
                    raise HTTPException(400, f"{target}: {exc}") from exc
    if not rows:
        raise HTTPException(400, "Нет доступных видео для выбранных папок")
    job_id = secrets.token_urlsafe(12)
    normalized = BatchPublishRequest(
        filenames=[row["filename"] for row in rows],
        source_folder=body.source_folder,
        targets=targets,
        interval_seconds=body.interval_seconds,
        interval_max_seconds=body.interval_max_seconds,
        wave_min_minutes=body.wave_min_minutes,
        wave_max_minutes=body.wave_max_minutes,
        shuffle_videos=body.shuffle_videos, hint=body.hint,
        captions=body.captions,
    )
    assignments = {
        row["source_folder"] + "/" + row["filename"]: [row["target"]]
        for row in rows
    }
    BATCH_JOBS[job_id] = {
        "id": job_id, "status": "queued",
        "created_at": int(time.time()),
        "started_at": None, "finished_at": None,
        "next_video_at": None, "completed_videos": 0,
        "total_videos": len(rows),
        "interval_seconds": body.interval_seconds,
        "interval_max_seconds": body.interval_max_seconds,
        "wave_min_minutes": body.wave_min_minutes,
        "wave_max_minutes": body.wave_max_minutes,
        "targets": targets, "cancel_requested": False,
        "assignment_mode": "one_video_one_account",
        "excluded_duplicate_files": duplicate_filenames,
        "cleanup_pending": [], "archived_count": 0, "error": "",
        "blocked_targets": {}, "assignments": assignments, "claimed_circles": [],
        "items": [{
            "filename": row["filename"], "source_folder": row["source_folder"],
            "status": "queued", "caption": normalized.captions.get(row["filename"], ""),
            "error": "", "targets": {row["target"]: {"status": "queued", "message": ""}},
        } for row in rows],
    }
    BATCH_JOBS[job_id]["request"] = normalized.model_dump()
    _save_batch_job(BATCH_JOBS[job_id])
    asyncio.create_task(_run_batch_job(job_id, normalized))
    return {"job_id": job_id, "status": "queued",
            "excluded_duplicates": len(duplicate_filenames)}


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
        return {"ok": True, "status": job["status"], "cancel_requested": False}
    job["cancel_requested"] = True
    # Persist immediately. The worker finishes its in-flight API request,
    # but will not start another video or restart on the next app launch.
    _save_batch_job(job)
    return {"ok": True, "status": job["status"], "cancel_requested": True}


@app.post("/api/publish-target")
async def publish_target(body: PublishTargetRequest):
    video = _safe_video(body.filename, body.source_folder)
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
    video = _safe_video(body.filename, body.source_folder)
    accounts = store.list_accounts()

    if body.targets:
        targets = body.targets
    else:
        targets = []
        default_platforms = ["youtube", "instagram"]
        if settings.tiktok_enabled or accounts.get("tiktok"):
            default_platforms.append("tiktok")
        for platform in default_platforms:
            for account in accounts.get(platform, []):
                targets.append(f"{platform}:{account['slot']}")

    if not targets:
        raise HTTPException(400, "Нет подключенных аккаунтов")
    if any(target.startswith("instagram:") for target in targets):
        try:
            load_instagram_caption_templates()
        except CaptionTemplatesError as exc:
            raise HTTPException(400, str(exc)) from exc

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
            if ((platform == "instagram" and (slot <= 0 or not store.get("instagram", slot)))
                    or (platform != "instagram" and slot not in (1, 2))):
                raise RuntimeError("Аккаунт не подключён или номер недопустим")

            if platform == "youtube":
                metadata = await asyncio.to_thread(
                    generate_youtube_metadata, video.name, body.caption
                )
                result = await youtube_upload(
                    slot, video, metadata["description"], title=metadata["title"]
                )
            elif platform == "tiktok":
                account = store.get("tiktok", slot) or {}
                if account.get("provider") == "autotok":
                    result = await autotok_bridge.publish(slot, video, body.caption)
                elif settings.tiktok_enabled:
                    result = await tiktok_upload(slot, video, body.caption)
                else:
                    raise RuntimeError("TikTok API отключён. Подключи аккаунт через AutoTok")
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
        destination_dir = archive_dir(body.source_folder)
        destination = destination_dir / video.name
        if destination.exists():
            destination = destination_dir / f"{video.stem}_{secrets.token_hex(3)}{video.suffix}"
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
    slots = set(range(1, 9)) | {int(account["slot"]) for account in store.list_accounts().get("instagram", [])}
    return {"covers": {str(slot): (COVER_DIR / f"{slot}.jpg").exists() for slot in sorted(slots)}}


@app.post("/api/instagram/covers/{slot}")
async def upload_instagram_cover(slot: int, file: UploadFile = File(...)):
    if slot < 1 or (slot > 8 and not store.get("instagram", slot)):
        raise HTTPException(400, "Instagram-аккаунт не подключён")
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
    if slot < 1 or (slot > 8 and not store.get("instagram", slot)):
        raise HTTPException(400, "Instagram-аккаунт не подключён")
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
<p class="muted">Обложки для подключённых аккаунтов Meta 1 и Meta 2</p></div><a href="/">← Назад к публикациям</a></header>
<div id="items"></div><script>
const root=document.getElementById('items');
async function load(){
  const [r, status]=await Promise.all([fetch('/api/instagram/covers'),fetch('/api/status')]);
  const d=await r.json();const accounts=(await status.json()).accounts.instagram||[];
  const bySlot=new Map(accounts.map(a=>[Number(a.slot),a]));
  const slots=[...new Set([...Array.from({length:8},(_,i)=>i+1),...accounts.map(a=>Number(a.slot))])].sort((a,b)=>a-b);
  root.replaceChildren();
  for(const i of slots){
    const a=bySlot.get(i);
    const el=document.createElement('section');
    const h=document.createElement('h3');
    h.textContent=a?((a.label||'Instagram')+' · Meta '+a.meta_app):(i<=8?'Свободное место · Instagram '+i:'Instagram');
    el.append(h);
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
def open_instagram_edge(
    slot: int,
    request: Request,
    mode: Literal["connect", "profile"] = "connect",
    meta_app: int = 1,
):
    # Launching local applications must never be possible through a public tunnel.
    if not request.client or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "Открывать Edge можно только из локального приложения")
    if slot < 1:
        raise HTTPException(400, "Некорректный идентификатор Instagram")
    if meta_app not in (1, 2):
        raise HTTPException(400, "Meta App должна быть 1 или 2")
    if mode == "connect":
        # New accounts may use the first Meta app in any of the eight slots.
        # The second Meta app remains optional for legacy 6–8 accounts.
        if meta_app == 2 and slot <= 5:
            raise HTTPException(400, "Meta 2 поддерживает слоты 6–8")
        if meta_app == 1 and not (settings.instagram_client_id and settings.instagram_client_secret):
            raise HTTPException(400, "Заполни INSTAGRAM_CLIENT_ID и INSTAGRAM_CLIENT_SECRET")
        if meta_app == 2 and not (settings.instagram_client_id_2 and settings.instagram_client_secret_2):
            raise HTTPException(400, "Заполни INSTAGRAM_CLIENT_ID_2 и INSTAGRAM_CLIENT_SECRET_2")
        url = f"http://127.0.0.1:{settings.port}/connect/instagram/{slot}?meta_app={meta_app}"
    else:
        account = store.get("instagram", slot)
        if not account:
            raise HTTPException(404, "Сначала подключи Instagram-аккаунт")
        username = account.get("username", "").strip().lstrip("@")
        url = f"https://www.instagram.com/{username}/" if username else "https://www.instagram.com/"
    profile_name = (
        "meta_2_shared" if slot >= 9 and (
            meta_app == 2 if mode == "connect"
            else (store.get("instagram", slot) or {}).get("meta_app") == 2
        )
        else f"slot_{slot}"
    )
    # A shared Edge profile for newly added Meta 2 accounts; all publishing
    # continues through independent Meta API tokens for each account.
    profile_dir = (settings.data_dir / "edge_instagram_profiles" / profile_name).resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.Popen(
            [
                _edge_executable(),
                f"--user-data-dir={profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "--new-window",
                url,
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise HTTPException(503, f"Не удалось запустить Microsoft Edge: {exc}") from exc
    return {"ok": True, "slot": slot, "mode": mode, "meta_app": meta_app if mode == "connect" else (store.get("instagram", slot) or {}).get("meta_app", 1 if slot <= 5 else 2)}


@app.post("/api/instagram/meta2/connect")
async def connect_new_meta2(request: Request):
    """Reserve hidden ID and provide a local OAuth link for an existing Dolphin profile.

    The app does not control Dolphin fingerprint, proxy, profiles, or credentials.
    User opens this URL in their own existing browser profile.
    """
    if not request.client or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "Подключать Instagram можно только локально")
    if not (settings.instagram_client_id_2 and settings.instagram_client_secret_2):
        raise HTTPException(400, "В .env не настроена вторая Meta App")
    async with META2_SLOT_LOCK:
        counter_path = settings.data_dir / "instagram_meta2_last_slot.txt"
        try:
            previous = int(counter_path.read_text(encoding="utf-8")) if counter_path.exists() else 8
        except (OSError, ValueError) as exc:
            raise HTTPException(500, "Не удалось прочитать счётчик Instagram аккаунтов Meta 2") from exc
        connected = [int(a["slot"]) for a in store.list_accounts().get("instagram", [])]
        slot = max([8, previous, *connected]) + 1
        counter_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = counter_path.with_suffix(".tmp")
        temp_path.write_text(str(slot), encoding="utf-8")
        temp_path.replace(counter_path)
    # Do not launch Edge or Dolphin: Dolphin profiles are user-managed.
    # Opening the local /connect URL in Dolphin initiates standard Meta 2 OAuth.
    return {
        "ok": True,
        "slot": slot,
        "url": f"http://127.0.0.1:{settings.port}/connect/instagram/{slot}?meta_app=2",
        "browser": "dolphin",
    }


@app.get("/connect/{platform}/{slot}")
async def connect(
    platform: Literal["youtube", "tiktok", "instagram"],
    slot: int,
    meta_app: int = 1,
    insights: bool = False,
):
    if (platform == "instagram" and slot < 1) or (platform != "instagram" and slot not in (1, 2)):
        raise HTTPException(400, "Недопустимый идентификатор аккаунта")
    _require_platform(platform)
    if platform == "instagram":
        if meta_app not in (1, 2):
            raise HTTPException(400, "Meta App должна быть 1 или 2")
        if meta_app == 2 and slot <= 5:
            raise HTTPException(400, "Meta 2 поддерживает слоты 6–8")
        if meta_app == 1 and not (settings.instagram_client_id and settings.instagram_client_secret):
            raise HTTPException(400, "Заполни INSTAGRAM_CLIENT_ID и INSTAGRAM_CLIENT_SECRET в .env")
        if meta_app == 2 and not (settings.instagram_client_id_2 and settings.instagram_client_secret_2):
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
        OAUTH_INSTAGRAM_META_APPS[state] = meta_app
        OAUTH_INSTAGRAM_INSIGHTS[state] = insights
        url = instagram_auth_url(state, slot, meta_app=meta_app, insights=insights)
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
    meta_app = OAUTH_INSTAGRAM_META_APPS.pop(state, 1 if slot <= 5 else 2)
    insights_requested = OAUTH_INSTAGRAM_INSIGHTS.pop(state, False)
    try:
        account = await instagram_exchange(code, slot, meta_app=meta_app)
        account["meta_app"] = meta_app
        account["insights_requested"] = insights_requested
        duplicate = next(
            (item for item in store.list_accounts().get("instagram", [])
             if str(item.get("id")) == str(account.get("id")) and int(item["slot"]) != slot),
            None,
        )
        if duplicate:
            raise HTTPException(
                409,
                f"Instagram @{account.get('username') or account.get('label') or 'account'} уже подключён. "
                "Выбери другой аккаунт в Edge перед новой авторизацией.",
            )
        store.save("instagram", slot, account)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Instagram OAuth: {exc}") from exc
    return RedirectResponse("/?connected=instagram")


@app.delete("/api/account/{platform}/{slot}")
async def disconnect(platform: Literal["youtube", "tiktok", "instagram"], slot: int):
    if (platform == "instagram" and slot < 1) or (platform != "instagram" and slot not in (1, 2)):
        raise HTTPException(400, "Недопустимый идентификатор аккаунта")
    # DELETE is idempotent. A repeated click/tab must not produce hundreds
    # of errors after the account was successfully removed.
    if not store.get(platform, slot):
        return {"ok": True, "already_disconnected": True}
    store.delete(platform, slot)
    return {"ok": True, "already_disconnected": False}


if __name__ == "__main__":
    # Keep one stable local process so browser-profile sessions and in-memory jobs
    # are not interrupted by development auto-reload.
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)
