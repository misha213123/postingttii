from __future__ import annotations

import asyncio
import hashlib
import secrets
import random
import shutil
import time
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException, File, UploadFile
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
from app.telegram_studio import router as telegram_studio_router, shutdown_telegram_studio
from app.instagram_studio import router as instagram_studio_router
from app.store import store
from app.account_creator.api.accounts import router as account_manager_router
from app.account_creator.api.aliases import router as account_aliases_router
from app.account_creator.api.profile import router as account_profile_router
from app.account_creator.api.browser_profiles import router as account_browser_router
from app.account_creator.api.tiktok import router as account_tiktok_router
from app.account_creator.api.instagram import router as account_instagram_router

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
app.include_router(instagram_studio_router)

@app.on_event("shutdown")
async def stop_telegram_studio():
    await shutdown_telegram_studio()

@app.get("/telegram-studio", response_class=HTMLResponse)
async def telegram_studio_page():
    return HTMLResponse((STATIC_DIR / "telegram-studio.html").read_text(encoding="utf-8"))

OAUTH_STATES: dict[str, tuple[str, int]] = {}
MEDIA_TOKENS: dict[str, Path] = {}
BATCH_JOBS: dict[str, dict] = {}


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


async def _publish_single_target(video: Path, caption: str, target: str, *, cycle_second: bool = False) -> dict:
    try:
        platform, slot_text = target.split(":", 1)
        slot = int(slot_text)
    except Exception as exc:
        raise HTTPException(400, "Некорректный target") from exc

    if slot not in (1, 2, 3, 4, 5):
        raise HTTPException(400, "Разрешены только слоты 1–5")
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
    if remaining > 0 and not cycle_second:
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
            if not cover_path.is_file():
                raise HTTPException(400, f"Instagram #{slot}: сначала сохрани обложку на /instagram-covers")
            cover_token = secrets.token_urlsafe(24)
            MEDIA_TOKENS[cover_token] = cover_path
            cover_url = f"{settings.public_base_url}/media/{cover_token}" if settings.public_base_url else ""
            if not cover_url:
                raise HTTPException(400, f"Instagram #{slot}: PUBLIC_BASE_URL не настроен, обложка недоступна Meta")
            cover_sha256 = hashlib.sha256(cover_path.read_bytes()).hexdigest()
            result = await instagram_upload(slot, video_url, caption, cover_url=cover_url)
            result["cover_slot"] = slot
            result["cover_sha256"] = cover_sha256
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
            if 'cover_token' in locals():
                asyncio.create_task(_expire_media_token(cover_token, 300))
        else:
            MEDIA_TOKENS.pop(media_token, None)


def _cleanup_published_batch_files(job: dict) -> None:
    """Remove rendered clips only when every selected target has published them.

    Publication history is persisted separately, so deleting the MP4 does not
    erase duplicate detection. Never delete a file used by another active job.
    """
    for item in job["items"]:
        if not all(
            item["targets"][target]["status"] in {"done", "already"}
            for target in job["targets"]
        ):
            continue
        filename = item["filename"]
        if any(
            other is not job
            and other.get("status") not in {"done", "cancelled", "error"}
            and filename in (other.get("filenames") or [])
            for other in BATCH_JOBS.values()
        ):
            continue
        path = settings.upload_dir / filename
        try:
            if path.is_file() and path.parent.resolve() == settings.upload_dir.resolve():
                path.unlink()
                item["file_deleted"] = True
        except OSError as exc:
            item["cleanup_error"] = str(exc)


async def _run_batch_job(job_id: str, body: BatchPublishRequest) -> None:
    """Preflight every account/video pair before publishing in parallel waves."""
    job = BATCH_JOBS[job_id]
    job["status"] = "checking"
    job["started_at"] = int(time.time())
    job["events"] = []
    job["checked_pairs"] = 0
    job["skipped_pairs"] = 0
    job["total_pairs"] = len(body.filenames) * len(body.targets)

    def event(kind: str, message: str, filename: str = "", target: str = "") -> None:
        job["events"].append({
            "at": int(time.time()), "kind": kind, "message": message,
            "filename": filename, "target": target,
        })

    # Check all pairs up front, not one clip at a time after waiting.
    pending = {target: [] for target in body.targets}
    for index, filename in enumerate(body.filenames):
        if job.get("cancel_requested"):
            job["status"] = "cancelled"
            return
        video = _safe_video(filename)
        key = await asyncio.to_thread(publish_state.video_key, video)
        item = job["items"][index]
        for target in body.targets:
            state = item["targets"][target]
            already = publish_state.is_completed(key, target)
            job["checked_pairs"] += 1
            if already:
                state.update(status="already", message="Уже опубликовано — пропущено")
                job["skipped_pairs"] += 1
                event("already", "Пропущен повтор", filename, target)
            else:
                pending[target].append((index, filename))
        if all(x["status"] == "already" for x in item["targets"].values()):
            item["status"] = "done"
            item["finished_at"] = int(time.time())
    job["preflight_complete"] = True
    event("checked", "Проверка всех роликов завершена")
    if not any(pending.values()):
        job["status"] = "done"
        job["completed_videos"] = len(body.filenames)
        job["finished_at"] = int(time.time())
        return

    async def publish_one(target: str, index: int, filename: str, *, cycle_second: bool = False) -> bool:
        item = job["items"][index]
        state = item["targets"][target]
        if job.get("cancel_requested"):
            return False
        video = _safe_video(filename)
        item["status"] = "publishing"
        state.update(status="publishing", message="Публикация")
        event("publishing", "Начата публикация", filename, target)
        try:
            manual = (body.captions.get(filename) or "").strip()
            caption = manual or await asyncio.to_thread(
                generate_caption, filename, body.hint, target
            )
            state["caption"] = caption
            result = await _publish_single_target(video, caption, target, cycle_second=cycle_second)
            if result.get("skipped"):
                state.update(status="already", message="Уже опубликовано")
                event("already", "Пропущен повтор", filename, target)
                return False
            if result.get("ok"):
                state.update(status="done", message="Опубликовано", progress=100)
                event("done", "Успешно опубликовано", filename, target)
                # History has already been saved by _publish_single_target.
                # Remove only the successful file; never delete on an error.
                if not any(
                    other is not job
                    and other.get("status") not in {"done", "cancelled", "error"}
                    and filename in (other.get("filenames") or [])
                    for other in BATCH_JOBS.values()
                ):
                    try:
                        video.unlink(missing_ok=True)
                        item["file_deleted"] = True
                        event("deleted", "Опубликованный файл удалён из очереди", filename, target)
                    except OSError as exc:
                        item["cleanup_error"] = str(exc)
                return True
            raise RuntimeError(result.get("error", "Ошибка публикации"))
        except HTTPException as exc:
            detail = exc.detail
            if exc.status_code == 429 and isinstance(detail, dict):
                state.update(status="cooldown", message=detail.get("message", "Пауза"),
                             retry_after_seconds=int(detail.get("retry_after_seconds", 0)))
            else:
                state.update(status="error", message=str(detail))
        except Exception as exc:
            state.update(status="error", message=str(exc))
        event("error", state["message"], filename, target)
        return False

    # Reserve two distinct videos per account in each wave. Each account
    # uploads its pair sequentially without a pause; accounts run in parallel.
    # Never assign one video to more than one account across the whole job.
    assigned_videos = set()
    job["status"] = "running"
    wave = 0
    while any(pending.values()):
        if job.get("cancel_requested"):
            job["status"] = "cancelled"
            job["finished_at"] = int(time.time())
            return
        wave += 1
        job["wave"] = wave
        assignments = {}
        for target, queue in pending.items():
            queue[:] = [(idx, name) for idx, name in queue
                        if name not in assigned_videos]
            pair = []
            while queue and len(pair) < 2:
                index, filename = queue.pop(0)
                if filename in assigned_videos:
                    continue
                assigned_videos.add(filename)
                pair.append((index, filename))
            if pair:
                assignments[target] = pair

        if not assignments:
            break

        async def publish_pair(target: str, pair: list) -> bool:
            succeeded = False
            for position, (index, filename) in enumerate(pair):
                if job.get("cancel_requested"):
                    break
                # The batch scheduler owns the pause between waves. Its local
                # per-account cooldown must not reject either clip in a pair,
                # including the first clip after a previous batch run.
                ok = await publish_one(
                    target, index, filename, cycle_second=True
                )
                succeeded = succeeded or ok
            return succeeded

        results = await asyncio.gather(*[
            publish_pair(target, pair)
            for target, pair in assignments.items()
        ])
        published_any = any(results)

        for item in job["items"]:
            statuses = [x["status"] for x in item["targets"].values()]
            if all(x in {"done", "already"} for x in statuses):
                item["status"] = "done"
                item.setdefault("finished_at", int(time.time()))
            elif any(x in {"error", "cooldown"} for x in statuses):
                item["status"] = "partial"
        job["completed_videos"] = sum(
            x["status"] in {"done", "partial"} for x in job["items"]
        )
        if any(pending.values()) and published_any:
            job["status"] = "waiting"
            job["next_video_at"] = int(time.time() + body.interval_minutes * 60)
            event("waiting", "Пауза между циклами")
            while time.time() < job["next_video_at"]:
                if job.get("cancel_requested"):
                    job["status"] = "cancelled"
                    job["finished_at"] = int(time.time())
                    return
                await asyncio.sleep(min(5, max(0.2, job["next_video_at"] - time.time())))
            job["next_video_at"] = None
            job["status"] = "running"
    job["status"] = "done"
    job["finished_at"] = int(time.time())
    event("finished", "Очередь завершена")
    # Successful clips were removed immediately after confirmed publication.
    # Failed clips remain on disk for review or retry.


@app.post("/api/batch/start")
async def batch_start(body: BatchPublishRequest):
    filenames = list(dict.fromkeys(Path(x).name for x in body.filenames))
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
    if body.interval_minutes > 24 * 60:
        raise HTTPException(400, "Слишком большой интервал")

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

    missing_covers = [
        target for target in targets
        if target.startswith("instagram:")
        and not (COVER_DIR / f"{target.split(':', 1)[1]}.jpg").is_file()
    ]
    if missing_covers:
        raise HTTPException(
            400,
            "Перед запуском добавь обложки для выбранных аккаунтов: "
            + ", ".join(missing_covers)
            + ". Открой /instagram-covers",
        )

    job_id = secrets.token_urlsafe(12)
    normalized = BatchPublishRequest(
        filenames=filenames,
        targets=targets,
        interval_minutes=body.interval_minutes,
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
        "targets": targets,
        "filenames": filenames,
        "cancel_requested": False,
        "error": "",
        "blocked_targets": {},
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
            if slot not in (1, 2, 3, 4, 5):
                raise RuntimeError("Разрешены только слоты 1–5")

            if platform == "youtube":
                result = await youtube_upload(slot, video, body.caption)
            elif platform == "tiktok":
                if not settings.tiktok_enabled:
                    raise RuntimeError("TikTok временно отключен")
                result = await tiktok_upload(slot, video, body.caption)
            elif platform == "instagram":
                result = await instagram_upload(slot, video_url, body.caption)
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
    return {"covers": {str(slot): (COVER_DIR / f"{slot}.jpg").exists() for slot in range(1, 6)}, "accounts": store.list_accounts().get("instagram", [])}


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


@app.get("/api/instagram/covers/{slot}/preview")
async def instagram_cover_preview(slot: int):
    if slot not in range(1, 6):
        raise HTTPException(400, "Неверный слот")
    path = COVER_DIR / f"{slot}.jpg"
    if not path.is_file():
        raise HTTPException(404, "Обложка отсутствует")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/instagram-covers")
async def instagram_covers_page():
    return HTMLResponse("""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Instagram — обложки</title>
<style>
:root{color-scheme:dark;font-family:Inter,system-ui,-apple-system,sans-serif}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 80% -10%,#17254a 0%,transparent 45%),#090f1b;color:#edf2ff}
main{max-width:1200px;margin:0 auto;padding:34px 22px 65px}
header{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}
h1{font-size:clamp(25px,3vw,36px);margin:0 0 8px;letter-spacing:-.8px}
p{color:#aab8d2;margin:0}.back{color:#d4dcff;text-decoration:none;border:1px solid #334363;padding:11px 17px;border-radius:11px}
.stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin:27px 0}
.stat{background:#101c31;border:1px solid #263a5c;border-radius:16px;padding:18px}.stat strong{display:block;font-size:25px}.stat span{font-size:13px;color:#a9bbd5}
#items{display:grid;gap:15px}
.cover-card{display:grid;grid-template-columns:230px 135px minmax(190px,1fr);align-items:center;gap:24px;padding:20px;background:linear-gradient(120deg,#141e34,#10192a);border:1px solid #2b3c5c;border-radius:18px;box-shadow:0 8px 28px #0003}
.cover-card:nth-child(5n+1){border-left:4px solid #bd65f6}.cover-card:nth-child(5n+2){border-left:4px solid #477efa}.cover-card:nth-child(5n+3){border-left:4px solid #2fd1a0}.cover-card:nth-child(5n+4){border-left:4px solid #ffb449}.cover-card:nth-child(5n){border-left:4px solid #9b68fb}
.identity{display:flex;gap:14px;align-items:center;min-width:0}.number{background:#6f54d9;padding:9px 12px;border-radius:10px;font-weight:800}.handle{font-size:18px;font-weight:750;overflow-wrap:anywhere}.sub{color:#96abc8;font-size:12px;margin-top:6px}
.preview{width:124px;height:165px;object-fit:cover;border-radius:12px;border:1px solid #48567c;background:#1b2942}.placeholder{width:124px;height:165px;display:flex;align-items:center;justify-content:center;border-radius:12px;border:1px dashed #50607e;color:#93a5c0;text-align:center;font-size:13px;padding:8px}
.actions{display:flex;flex-direction:column;align-items:flex-start;gap:10px}.status{font-size:13px;color:#44e2a4}.status.empty{color:#ffbc70}
button,.upload{background:#6859e7;border:1px solid #8177ff;border-radius:10px;color:white;padding:11px 16px;font-size:14px;font-weight:650;cursor:pointer}
button:hover,.upload:hover{filter:brightness(1.12)}button:disabled{opacity:.6;cursor:wait}
.upload input{display:none}.hint{font-size:12px;color:#96abc8}.error{color:#ff8d9c;font-size:12px}
@media(max-width:700px){.stats{grid-template-columns:1fr}.cover-card{grid-template-columns:1fr 124px;gap:14px}.identity{grid-column:1/-1}.actions{align-self:start}}
</style></head><body><main>
<header><div><h1>▣ Обложки Instagram Reels</h1><p>Каждый аккаунт — своя обложка. Здесь видно именно сохранённое изображение.</p></div><a class="back" href="/">← К публикациям</a></header>
<div class="stats"><div class="stat"><strong id="connected">—</strong><span>Подключено аккаунтов</span></div><div class="stat"><strong id="saved">—</strong><span>Обложек сохранено</span></div><div class="stat"><strong id="ready">—</strong><span>Готовность выбранных слотов</span></div></div>
<div id="items"></div></main>
<script>
const root=document.getElementById('items');
function card(slot,account,exists){
 const el=document.createElement('section');el.className='cover-card';
 const name=account?(account.label||'Подключённый аккаунт'):'Не подключён';
 const id=document.createElement('div');id.className='identity';
 const num=document.createElement('span');num.className='number';num.textContent=slot;
 const info=document.createElement('div');const title=document.createElement('div');title.className='handle';title.textContent=name;
 const sub=document.createElement('div');sub.className='sub';sub.textContent='Instagram #'+slot+' · '+(account?'Аккаунт подключён':'Требуется подключение');
 info.append(title,sub);id.append(num,info);
 const imageWrap=document.createElement('div');
 const status=document.createElement('div');status.className='status'+(exists?'':' empty');status.textContent=exists?'✓ Обложка сохранена':'Обложка не загружена';
 const controls=document.createElement('div');controls.className='actions';
 const label=document.createElement('label');label.className='upload';label.textContent=exists?'↥ Заменить обложку':'↥ Загрузить обложку';
 const input=document.createElement('input');input.type='file';input.accept='image/png,image/jpeg';label.append(input);
 const hint=document.createElement('div');hint.className='hint';hint.textContent='JPG или PNG · до 8 МБ';
 const message=document.createElement('div');message.className='error';
 function show(url){imageWrap.replaceChildren();const img=document.createElement('img');img.className='preview';img.alt='Обложка аккаунта '+name;img.src=url;img.onerror=()=>{imageWrap.textContent='Не удалось показать обложку';};imageWrap.append(img);}
 if(exists)show('/api/instagram/covers/'+slot+'/preview?v='+Date.now());
 else{const ph=document.createElement('div');ph.className='placeholder';ph.textContent='Нет обложки';imageWrap.append(ph);}
 input.onchange=async()=>{
  const file=input.files[0];if(!file)return;
  message.textContent='';
  if(file.size>8*1024*1024){message.textContent='Файл больше 8 МБ';return;}
  label.style.opacity='.6';const data=new FormData();data.append('file',file);
  try{const response=await fetch('/api/instagram/covers/'+slot,{method:'POST',body:data});
   if(!response.ok)throw Error(await response.text());
   show('/api/instagram/covers/'+slot+'/preview?v='+Date.now());
   status.textContent='✓ Обложка сохранена';status.className='status';
   label.firstChild.textContent='↥ Заменить обложку';await refreshCounters();
  }catch(e){message.textContent='Не удалось сохранить: '+e.message;}
  finally{label.style.opacity='1';input.value='';}
 };
 controls.append(status,label,hint,message);el.append(id,imageWrap,controls);root.append(el);
}
async function refreshCounters(){
 const r=await fetch('/api/instagram/covers',{cache:'no-store'});if(!r.ok)throw Error('Ошибка загрузки');
 const d=await r.json();const accounts=new Map((d.accounts||[]).map(a=>[Number(a.slot),a]));
 const count=[1,2,3,4,5].filter(x=>accounts.has(x)).length;
 const saved=[1,2,3,4,5].filter(x=>d.covers[String(x)]).length;
 document.getElementById('connected').textContent=count+' / 5';
 document.getElementById('saved').textContent=saved+' / 5';
 document.getElementById('ready').textContent=[1,2,3,4,5].every(x=>accounts.has(x)&&d.covers[String(x)])?'✓ Готово':'Нужны настройки';
 return {accounts,d};
}
async function init(){try{const {accounts,d}=await refreshCounters();root.replaceChildren();for(let slot=1;slot<=5;slot++)card(slot,accounts.get(slot),!!d.covers[String(slot)]);}
 catch(e){root.textContent='Не удалось загрузить настройки: '+e.message;}}
init();
</script></body></html>""")


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
    if slot not in (1, 2, 3, 4, 5):
        raise HTTPException(400, "Слот должен быть от 1 до 5")
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
    if slot not in (1, 2, 3, 4, 5):
        raise HTTPException(400, "Слот должен быть от 1 до 5")
    store.delete(platform, slot)
    return {"ok": True}


if __name__ == "__main__":
    # Keep one stable local process so browser-profile sessions and in-memory jobs
    # are not interrupted by development auto-reload.
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)
