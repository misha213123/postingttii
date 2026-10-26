"""Opt-in direct browser Instagram posting with local account profiles.

Independent from Meta Graph; users log in interactively using a normal,
visible browser. No credential harvesting, captcha solving, proxy-based evasion, or bypass of
Instagram verification. All configuration and browser state is local to the PC.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import secrets
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import settings
from app.store import store
from app.video_folders import folder_path, list_videos, video_path, _read as folder_settings
from app.browser_proxy import parse_proxy, masked_proxy, check_proxy
from app.upload_cleanup import move_confirmed_video

router = APIRouter(prefix="/api/browser-instagram", tags=["browser-instagram"])
DATA_DIR = settings.data_dir / "browser_instagram"
PROFILES_DIR = DATA_DIR / "profiles"
ACCOUNTS_FILE = DATA_DIR / "accounts.enc"
COVERS_DIR = DATA_DIR / "covers"
COVER_LIMIT = 8 * 1024 * 1024

# The Playwright driver is intentionally held for the life of the local app.
PLAYWRIGHT = None
CONTEXTS: dict[str, Any] = {}
CAMOUFOX_HANDLES: dict[str, Any] = {}
OPEN_LOCKS: dict[str, asyncio.Lock] = {}
PUBLISH_LOCK = asyncio.Lock()
CURRENT_JOB: dict[str, Any] | None = None
CURRENT_TASK: asyncio.Task | None = None
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v"}
USER_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
log = logging.getLogger(__name__)


def _brief_error(exc: Exception) -> str:
    """Never expose Playwright proxy URLs, cookie data or credentials."""
    if isinstance(exc, HTTPException):
        return str(exc.detail)
    if isinstance(exc, RuntimeError):
        # Only our explicitly-authored workflow errors use RuntimeError;
        # external Playwright errors need a generic sanitized explanation.
        message = str(exc)
        if message.startswith(("Требуется ", "Instagram ", "Не найден ", "Не найдена ", "Не нашёл ", "Проверь ", "Не удалось ", "Видео ", "Формат ", "Обложка ")):
            return message
    return "Ошибка взаимодействия с Edge/Camoufox или Instagram. Проверь браузер, вход и подключение."


def _stage(item: dict, stage: str, label: str) -> None:
    item["step"] = stage
    item["step_label"] = label




def _local(request: Request) -> None:
    if not request.client or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "Браузерный постинг доступен только локально")
    # Block cross-site requests to the local process from unrelated websites.
    origin = request.headers.get("origin", "")
    if origin and origin not in {
        f"http://127.0.0.1:{settings.port}",
        f"http://localhost:{settings.port}",
    }:
        raise HTTPException(403, "Недопустимый источник запроса")


def _read() -> dict[str, dict]:
    try:
        raw = store._fernet.decrypt(ACCOUNTS_FILE.read_bytes())
        data = json.loads(raw)
        if isinstance(data, dict) and isinstance(data.get("accounts"), dict):
            return data["accounts"]
    except (OSError, ValueError, TypeError):
        pass
    return {}


def _write(data: dict[str, dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp = ACCOUNTS_FILE.with_suffix(".tmp")
    temp.write_bytes(store._fernet.encrypt(
        json.dumps({"accounts": data}, ensure_ascii=False).encode("utf-8")
    ))
    os.replace(temp, ACCOUNTS_FILE)
    try:
        os.chmod(ACCOUNTS_FILE, 0o600)
    except OSError:
        pass


def _public_account(account_id: str, data: dict) -> dict:
    browser = data.get("browser", "edge")
    external = data.get("profile_path", "") if browser == "camoufox" else ""
    profile = (
        Path(os.path.expandvars(os.path.expanduser(external)))
        if external else
        (PROFILES_DIR / account_id / "camoufox" if browser == "camoufox"
         else PROFILES_DIR / account_id)
    )
    return {
        "id": account_id, "username": data["username"],
        "browser": browser, "profile_path": external,
        "proxy_configured": bool(data.get("proxy")),
        "proxy_label": masked_proxy(data.get("proxy", "")),
        "browser_open": account_id in CONTEXTS,
        "has_profile": profile.is_dir(),
    }


def _account(account_id: str) -> dict:
    a = _read().get(account_id)
    if not a:
        raise HTTPException(404, "Браузерный Instagram-аккаунт не найден")
    return a


def _profile_dir(account_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{24}", account_id):
        raise HTTPException(400, "Неверный идентификатор профиля")
    return PROFILES_DIR / account_id


def _external_profile(value: str) -> str:
    """Link to the user's existing persistent Camoufox profile without copying it."""
    raw = value.strip()
    if not raw:
        return ""
    path = Path(os.path.expandvars(os.path.expanduser(raw))).resolve()
    home = Path.home().resolve()
    if home not in path.parents or not path.is_dir():
        raise HTTPException(
            400, "Профиль Camoufox должен существовать внутри домашней папки Windows. "
                 "Проверь путь и закрой старый Camoufox перед подключением.",
        )
    return str(path)


def _browser_name(account_id: str) -> str:
    return "Camoufox" if _read().get(account_id, {}).get("browser") == "camoufox" else "Edge"


class BrowserAccount(BaseModel):
    username: str = Field(min_length=1, max_length=30)
    proxy: str = Field(default="", max_length=1200)
    browser: Literal["edge", "camoufox"] = "edge"
    profile_path: str = Field(default="", max_length=1024)


class ProxyInput(BaseModel):
    proxy: str = Field(default="", max_length=1200)


class PublishInput(BaseModel):
    accounts: list[str] = Field(min_length=1, max_length=25)
    folder_id: str = "default"
    filenames: list[str] = Field(min_length=1, max_length=100)
    caption: str = Field(default="", max_length=2200)
    covers: dict[str, str] = Field(default_factory=dict)
    require_covers: bool = False
    # Require explicit user confirmation of auto Share on selected accounts.
    confirmed: bool = False


@router.get("")
def status(request: Request):
    _local(request)
    folders = [{"id": "default", "label": "Стандартная videos/inbox"}]
    for fid, record in folder_settings()["folders"].items():
        if isinstance(record, dict):
            folders.append({"id": fid, "label": str(record.get("label") or fid)})
    return {
        "accounts": [_public_account(key, a) for key, a in _read().items()],
        "folders": folders,
        "job": dict(CURRENT_JOB) if CURRENT_JOB else None,
    }


def _cover_path(cover_id: str) -> Path:
    """Never accept a user-supplied path as a cover reference."""
    if not re.fullmatch(r"[a-f0-9]{24}", cover_id):
        raise HTTPException(400, "Некорректный идентификатор обложки")
    path = COVERS_DIR / (cover_id + ".jpg")
    if not path.is_file():
        raise HTTPException(400, "Обложка не найдена. Выбери файл заново.")
    return path


@router.post("/cover", status_code=201)
async def upload_cover(request: Request, file: UploadFile = File(...)):
    """One cover image per prepared Reel, kept only on local disk."""
    _local(request)
    if file.content_type not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(400, "Обложка должна быть JPG, PNG или WebP")
    try:
        data = await file.read(COVER_LIMIT + 1)
    finally:
        await file.close()
    if not data or len(data) > COVER_LIMIT:
        raise HTTPException(400, "Размер обложки должен быть от 1 байта до 8 МБ")
    try:
        with Image.open(io.BytesIO(data)) as original:
            if original.format not in ("JPEG", "PNG", "WEBP"):
                raise HTTPException(400, "Неподдерживаемый формат изображения")
            if original.width < 180 or original.height < 320:
                raise HTTPException(400, "Обложка слишком маленькая. Выбери изображение 9:16.")
            picture = ImageOps.exif_transpose(original).convert("RGB")
            # Make the output consistently 1080x1920 for the Instagram Reel.
            cover = ImageOps.fit(
                picture, (1080, 1920), method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
        COVERS_DIR.mkdir(parents=True, exist_ok=True)
        cover_id = secrets.token_hex(12)
        cover.save(COVERS_DIR / (cover_id + ".jpg"), format="JPEG", quality=88,
                   optimize=True, subsampling=0)
    except HTTPException:
        raise
    except (UnidentifiedImageError, ValueError, OSError, Image.DecompressionBombError):
        raise HTTPException(400, "Не удалось прочитать обложку. Нужен JPG/PNG/WebP.") from None
    return {
        "ok": True, "cover_id": cover_id,
        "preview_url": "/api/browser-instagram/cover/" + cover_id,
        "width": 1080, "height": 1920,
    }


@router.get("/cover/{cover_id}")
def preview_cover(cover_id: str, request: Request):
    _local(request)
    return FileResponse(_cover_path(cover_id), media_type="image/jpeg",
                        headers={"Cache-Control": "private, no-store"})


@router.post("/account")
def add_account(body: BrowserAccount, request: Request):
    _local(request)
    username = body.username.strip().lstrip("@").lower()
    if not USER_RE.fullmatch(username):
        raise HTTPException(400, "Введи корректный Instagram username")
    try:
        parse_proxy(body.proxy)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    profile_path = _external_profile(body.profile_path) if body.browser == "camoufox" else ""
    if body.browser != "camoufox" and body.profile_path.strip():
        raise HTTPException(400, "Внешний профиль поддерживается только для Camoufox")
    data = _read()
    existing = next((key for key, a in data.items() if a["username"] == username), None)
    if existing:
        raise HTTPException(409, "Аккаунт уже добавлен")
    if profile_path and any(
        a.get("profile_path") == profile_path for a in data.values()
    ):
        raise HTTPException(409, "Этот профиль Camoufox уже подключён")
    key = secrets.token_hex(12)
    data[key] = {
        "username": username, "proxy": body.proxy.strip(),
        "browser": body.browser, "profile_path": profile_path,
    }
    _write(data)
    return _public_account(key, data[key])


@router.put("/account/{account_id}/proxy")
def set_account_proxy(account_id: str, body: ProxyInput, request: Request):
    """Proxy changes take effect on next browser launch, never mid-session."""
    _local(request)
    if account_id in CONTEXTS:
        raise HTTPException(409, "Сначала нажми «Закрыть» у аккаунта, чтобы поменять прокси")
    if CURRENT_JOB and CURRENT_JOB.get("status") == "running":
        raise HTTPException(409, "Не меняй прокси во время активной публикации")
    try:
        parse_proxy(body.proxy)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    data = _read()
    if account_id not in data:
        raise HTTPException(404, "Браузерный аккаунт не найден")
    data[account_id]["proxy"] = body.proxy.strip()
    _write(data)
    return _public_account(account_id, data[account_id])


@router.post("/proxy/test")
async def check_new_proxy(body: ProxyInput, request: Request):
    _local(request)
    try:
        return await check_proxy(body.proxy)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/account/{account_id}/proxy/test")
async def check_saved_proxy(account_id: str, request: Request):
    _local(request)
    account = _account(account_id)
    if not account.get("proxy"):
        raise HTTPException(400, "У аккаунта ещё не задан прокси")
    try:
        return await check_proxy(account["proxy"])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/account/{account_id}")
async def delete_account(account_id: str, request: Request):
    _local(request)
    if CURRENT_JOB and CURRENT_JOB.get("status") == "running":
        raise HTTPException(409, "Дождись завершения публикации")
    await close_account_context(account_id)
    data = _read()
    data.pop(account_id, None)
    _write(data)
    # Saved Chrome profiles are deliberately retained unless user deletes
    # data/browser_instagram/profiles manually. Avoid losing browser login.
    return {"ok": True}


@router.get("/videos")
def videos(request: Request, folder_id: str = "default"):
    _local(request)
    try:
        clips = list_videos(folder_id)
    except (ValueError, OSError) as exc:
        raise HTTPException(400, "Выбранная папка недоступна") from exc
    return {
        "folder_id": folder_id,
        "videos": [{"name": p.name, "size_mb": round(p.stat().st_size/1048576, 1)}
                   for p in clips if p.suffix.lower() in VIDEO_SUFFIXES],
    }


async def _driver():
    global PLAYWRIGHT
    if PLAYWRIGHT is None:
        from playwright.async_api import async_playwright
        PLAYWRIGHT = await async_playwright().start()
    return PLAYWRIGHT


async def _close_camoufox_handle(manager: Any):
    try:
        await manager.__aexit__(None, None, None)
    except Exception:
        log.warning("Camoufox profile cleanup failed")


async def _context(account_id: str):
    account = _account(account_id)
    if account_id in CONTEXTS:
        return CONTEXTS[account_id]
    lock = OPEN_LOCKS.setdefault(account_id, asyncio.Lock())
    async with lock:
        if account_id in CONTEXTS:
            return CONTEXTS[account_id]
        engine = account.get("browser", "edge")
        if engine == "camoufox":
            linked = account.get("profile_path", "")
            profile = (Path(_external_profile(linked)) if linked else
                       _profile_dir(account_id) / "camoufox")
        else:
            profile = _profile_dir(account_id)
        profile.mkdir(parents=True, exist_ok=True)
        opts = {}
        if account.get("proxy"):
            try:
                proxy = parse_proxy(account["proxy"])
            except ValueError:
                raise HTTPException(400, "У аккаунта сохранён неверный прокси") from None
            if proxy:
                opts["proxy"] = proxy
        if engine == "camoufox":
            try:
                from camoufox.async_api import AsyncCamoufox
            except ImportError:
                raise HTTPException(
                    503, "Camoufox не установлен в PostingTTII. Выполни pip install "
                         "-r requirements.txt и python -m camoufox fetch.",
                ) from None
            manager = AsyncCamoufox(
                headless=False, os="windows", persistent_context=True,
                user_data_dir=str(profile), **opts,
            )
            try:
                context = await manager.__aenter__()
            except Exception:
                raise HTTPException(
                    503, "Не удалось открыть Camoufox. Закрой прежний Camoufox и launch.py "
                         "с этим профилем, проверь браузер и прокси.",
                ) from None
            CAMOUFOX_HANDLES[account_id] = manager
        else:
            driver = await _driver()
            try:
                context = await driver.chromium.launch_persistent_context(
                    str(profile), channel="msedge", headless=False,
                    viewport={"width": 1280, "height": 900},
                    locale="en-US", accept_downloads=False, **opts,
                )
            except Exception:
                raise HTTPException(
                    503, "Не удалось открыть Edge. Закрой старый профиль и проверь Playwright.",
                ) from None
        CONTEXTS[account_id] = context

        def on_close(*_):
            if CONTEXTS.get(account_id) is context:
                CONTEXTS.pop(account_id, None)
                handle = CAMOUFOX_HANDLES.pop(account_id, None)
                if handle:
                    asyncio.create_task(_close_camoufox_handle(handle))

        if callable(getattr(context, "on", None)):
            context.on("close", on_close)
        return context


async def close_account_context(account_id: str):
    context = CONTEXTS.pop(account_id, None)
    manager = CAMOUFOX_HANDLES.pop(account_id, None)
    if manager:
        await _close_camoufox_handle(manager)
    elif context:
        try:
            await context.close()
        except Exception:
            pass


@router.post("/account/{account_id}/open")
async def open_account(account_id: str, request: Request):
    _local(request)
    try:
        # Opening a profile during an active upload must NOT navigate the
        # same tab away from the Share workflow (this previously cancelled
        # in-progress browser uploads).
        active_upload = (
            CURRENT_JOB is not None and CURRENT_JOB.get("status") == "running"
            and account_id in CURRENT_JOB.get("accounts", [])
        )
        existing_context = account_id in CONTEXTS
        if active_upload and not existing_context:
            raise HTTPException(409, "Этот браузер сейчас готовится к публикации. Открой его после запуска.")
        ctx = await _context(account_id)
        browser_name = _browser_name(account_id)
        # Reuse an Instagram tab, not an unrelated tab with a 2FA provider.
        page = next((p for p in ctx.pages if "instagram.com" in p.url), None)
        if page is None:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        if existing_context or active_upload:
            # Also preserve the failed upload page after the job is stopped:
            # clicking 'Open Edge' should reveal the error, not erase it.
            await page.bring_to_front()
            return {
                "ok": True,
                "message": ("Публикация выполняется — окно выведено вперёд без обновления."
                            if active_upload else
                            f"{browser_name} уже открыт. Страница сохранена для проверки ошибки."),
            }
        try:
            await page.goto("https://www.instagram.com/",
                            wait_until="domcontentloaded", timeout=45000)
        except Exception as exc:
            log.warning("Browser IG page navigation failed for profile %s: %s",
                        account_id[:8], type(exc).__name__)
            raise HTTPException(
                502, f"{browser_name} открыт, но Instagram не загрузился. Проверь прокси, "
                     f"подключение и страницу в окне {browser_name}.",
            ) from None
        await page.bring_to_front()
        return {"ok": True, "message": f"{browser_name} открыл Instagram. Проверь вход и свою страницу."}
    except HTTPException:
        raise
    except Exception as exc:
        log.error("Browser IG Open action failed for profile %s: %s",
                  account_id[:8], type(exc).__name__)
        await close_account_context(account_id)
        raise HTTPException(
            503, "Не удалось открыть профиль Edge/Camoufox. Возможно, окно было закрыто "
                 "вручную — попробуй открыть снова.",
        ) from None


@router.post("/account/{account_id}/close")
async def close_account(account_id: str, request: Request):
    _local(request)
    if CURRENT_JOB and CURRENT_JOB.get("status") == "running" and account_id in CURRENT_JOB.get("accounts", []):
        raise HTTPException(409, "Этот браузер используется текущей публикацией")
    await close_account_context(account_id)
    return {"ok": True}


def _locator_first(page, kind: str, names: str):
    regex = re.compile(names, re.I)
    return (page.get_by_role(kind, name=regex).first)


async def _click_action(page, text: str, timeout_ms: int = 15000):
    role = page.get_by_role("button", name=re.compile(r"^(" + text + r")$", re.I)).first
    try:
        await role.click(timeout=timeout_ms)
    except Exception:
        await page.get_by_text(re.compile(r"^(" + text + r")$", re.I)).last.click(timeout=timeout_ms)


async def _file_input_attached(page, timeout_ms: int = 1200) -> bool:
    """Check only the create dialog; does not submit or mutate the post."""
    try:
        await page.locator('input[type="file"]').first.wait_for(
            state="attached", timeout=timeout_ms,
        )
        return True
    except Exception:
        return False


async def _open_instagram_create(page) -> None:
    """Support both expanded and icon-only Instagram sidebar layouts.

    Instagram changes its navigation markup frequently. Prefer a visible,
    named Create link/button; fall back to the + icon's accessible SVG label.
    Only a single successful click is made.
    """
    if await _file_input_attached(page, timeout_ms=800):
        return  # The user already opened the create dialog.
    role_name = re.compile(r"^(Create|New post|Создать|Новая публикация)$", re.I)
    candidates = [
        page.locator('a[href="/create/select/"], a[href="/create/"], a[href^="/create/"]').first,
        page.get_by_role("link", name=role_name).first,
        page.get_by_role("button", name=role_name).first,
        page.locator(
            'svg[aria-label="New post"], svg[aria-label="Create"], '
            'svg[aria-label="Создать"], svg[aria-label="Новая публикация"]'
        ).first,
        page.get_by_text(role_name).first,
    ]
    for candidate in candidates:
        try:
            await candidate.click(timeout=2400)
            return
        except Exception:
            continue
    # Last resort: navigate only to Instagram's own creator URL. Navigation
    # cannot publish a video, and the next stage verifies the file chooser.
    try:
        await page.goto("https://www.instagram.com/create/select/",
                        wait_until="domcontentloaded", timeout=15000)
        if await _file_input_attached(page, timeout_ms=1800):
            return
    except Exception:
        pass
    raise RuntimeError("Не найдена кнопка «Создать» в Instagram.")


def _validate_vertical_reel(video: Path) -> tuple[int, int]:
    """Block a non-9:16 source before any Instagram Share click."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", str(video)],
            capture_output=True, text=True, timeout=20, check=True,
        )
        stream = json.loads(result.stdout)["streams"][0]
        width, height = int(stream["width"]), int(stream["height"])
    except (FileNotFoundError, subprocess.SubprocessError, KeyError,
            IndexError, ValueError, TypeError, json.JSONDecodeError):
        raise RuntimeError(
            "Не удалось проверить формат видео через FFprobe. "
            "Установи FFmpeg или проверь файл перед отправкой."
        ) from None
    if width <= 0 or height <= 0 or abs(width / height - 9 / 16) > 0.012:
        raise RuntimeError(
            f"Видео имеет размер {width}×{height}, а нужен вертикальный 9:16 "
            "(например, 1080×1920). Перерендери ролик в Студии PostingTTII."
        )
    return width, height


async def _select_vertical_crop(page) -> None:
    """Use Instagram's crop menu so a 9:16 video is not published as a square.

    Fail closed when the crop choice cannot be confirmed. We intentionally
    never infer that Instagram kept the original orientation automatically.
    """
    controls = [
        page.get_by_role("button", name=re.compile(
            r"Crop|Select crop|Aspect ratio|Original size|Обрез|Пропорци|Формат",
            re.I,
        )).first,
        page.locator(
            'svg[aria-label="Select crop"], svg[aria-label="Crop"], '
            'svg[aria-label="Select aspect ratio"], '
            'svg[aria-label="Adjust"], svg[aria-label="Обрезать"], '
            'svg[aria-label="Выбрать формат"]'
        ).first,
        page.locator(
            '[aria-label="Select crop"], [aria-label="Select aspect ratio"], '
            '[aria-label="Crop"], [aria-label="Обрезать"]'
        ).first,
    ]
    opened = False
    for control in controls:
        try:
            await control.click(timeout=3000)
            opened = True
            break
        except Exception:
            continue
    if not opened:
        raise RuntimeError(
            "Формат 9:16 не подтверждён: Instagram не показал меню пропорций. "
            "Ролик не отправлен, проверь окно Camoufox."
        )
    options = [
        page.get_by_text(re.compile(r"^9\s*:\s*16$", re.I)).last,
        page.get_by_role("button", name=re.compile(r"^9\s*:\s*16$")).first,
        page.get_by_text(re.compile(r"^Original$|^Оригинал$|^Исходный$", re.I)).last,
    ]
    for option in options:
        try:
            await option.click(timeout=2600)
            return
        except Exception:
            continue
    raise RuntimeError(
        "Формат 9:16 не подтверждён: нет пункта «9:16» или «Original». "
        "Ролик не отправлен, проверь окно Camoufox."
    )


async def _set_instagram_cover(page, cover: Path) -> None:
    """Upload the cover on Instagram's editing step, before final Next/Share.

    Do not use a generic file input: that could append the image to the post
    rather than set it as the Reel's cover.
    """
    control_name = re.compile(
        r"^Select from computer$|^Выбрать с компьютера$|"
        r"^Upload cover$|^Загрузить обложку$|^Choose cover$",
        re.I,
    )
    controls = [
        page.get_by_role("button", name=control_name).last,
        page.get_by_text(control_name).last,
        page.get_by_role("link", name=control_name).last,
    ]
    for control in controls:
        try:
            async with page.expect_file_chooser(timeout=5000) as chooser_info:
                await control.click(timeout=3000)
            chooser = await chooser_info.value
            await chooser.set_files(str(cover))
            return
        except Exception:
            continue
    raise RuntimeError(
        "Обложка не загружена: Instagram не показал кнопку выбора обложки "
        "на экране редактирования. Публикация остановлена без Share."
    )


async def _publish_via_browser(ctx, video: Path, caption: str, item: dict | None = None, *, cover: Path | None = None) -> str:
    """UI-based Instagram upload; stop on ambiguity, never retry Share."""
    item = item if item is not None else {}
    _stage(item, "open_instagram", "Открываю Instagram")
    try:
        pages = ctx.pages
        page = next((p for p in pages if "instagram.com" in p.url), None)
        if page is None:
            page = pages[0] if pages else await ctx.new_page()
    except Exception:
        raise RuntimeError("Не удалось подключиться к окну Edge. Закрой профиль и открой заново.") from None
    try:
        await page.goto("https://www.instagram.com/",
                        wait_until="domcontentloaded", timeout=45000)
    except Exception:
        raise RuntimeError(
            "Не удалось загрузить instagram.com. Проверь страницу в Edge "
            "и подключение через свой прокси."
        ) from None

    _stage(item, "login", "Проверяю вход")
    if await page.locator('input[name="username"], input[name="password"]').count():
        raise RuntimeError("Требуется ручной вход в Instagram — открой профиль.")
    # Never automate login, additional account verification or CAPTCHA.
    _stage(item, "create", "Открываю создание публикации")
    try:
        await _open_instagram_create(page)
    except Exception:
        raise RuntimeError(
            "Не найдена кнопка «Создать» в Instagram. В Camoufox/Edge проверь "
            "значок «+» слева и обнови страницу. Если Instagram изменил меню, "
            "публикация остановлена без отправки."
        ) from None

    _stage(item, "choose_video", "Выбираю файл видео")
    try:
        # Instagram may show the Post/Reel chooser instead of the upload form.
        # Prefer Reels for video, but never click Share at this stage.
        upload = page.locator('input[type="file"]').first
        if not await _file_input_attached(page, timeout_ms=1800):
            for name in (r"^Reel$|^Reels$|^Рилс$|^Видео Reels$",
                         r"^Post$|^Публикация$"):
                try:
                    await page.get_by_text(re.compile(name, re.I)).last.click(
                        timeout=2200
                    )
                    break
                except Exception:
                    continue
        await upload.wait_for(state="attached", timeout=20000)
        await upload.set_input_files(str(video), timeout=35000)
    except Exception:
        raise RuntimeError(
            "Не удалось выбрать видео в Instagram. Проверь окно загрузки "
            "и поддерживаемый формат файла."
        ) from None

    _stage(item, "aspect", "Проверяю 9:16 и выбираю полный вертикальный кадр")
    _validate_vertical_reel(video)
    await _select_vertical_crop(page)

    _stage(item, "next_1", "Открываю редактор обложки")
    try:
        await _click_action(page, r"Next|Далее", timeout_ms=65000)
    except Exception:
        raise RuntimeError(
            "Не удалось открыть редактор обложки после выбора 9:16."
        ) from None

    if cover is not None:
        _stage(item, "cover", "Устанавливаю выбранную заранее обложку")
        await _set_instagram_cover(page, cover)

    _stage(item, "next_2", "Перехожу к описанию видео")
    try:
        await _click_action(page, r"Next|Далее", timeout_ms=65000)
    except Exception:
        raise RuntimeError(
            "Не удалось перейти к описанию после выбора обложки."
        ) from None

    _stage(item, "caption", "Добавляю описание")
    if caption:
        textbox = page.locator(
            'textarea[aria-label*="caption" i], [contenteditable="true"][role="textbox"]'
        ).first
        try:
            await textbox.wait_for(state="visible", timeout=20000)
            await textbox.fill(caption)
        except Exception:
            raise RuntimeError(
                "Не нашёл поле описания. Проверь форму публикации в Edge."
            ) from None

    _stage(item, "share", "Отправляю видео")
    try:
        await _click_action(page, r"Share|Поделиться|Опубликовать", timeout_ms=20000)
    except Exception:
        # A click may have succeeded before Playwright timed out.
        _stage(item, "verify", "Проверь вручную — результат отправки неизвестен")
        return "needs_review"

    _stage(item, "verify", "Жду подтверждения Instagram")
    try:
        await page.get_by_text(re.compile(
            r"Your post has been shared|Post shared|Публикация опубликована|"
            r"Your reel has been shared|Your reel has been posted",
            re.I,
        )).first.wait_for(state="visible", timeout=110000)
        _stage(item, "done", "Подтверждено Instagram")
        return "published"
    except Exception:
        return "needs_review"


async def _run_job(job: dict, selected: list[tuple[str, Path]], caption: str, covers: dict[str, Path] | None = None):
    # A selected source can target multiple Instagram accounts. Clear it from
    # the folder only after every selected upload of this source is confirmed.
    remaining = Counter(path.resolve() for _, path in selected)
    uncertain: set[Path] = set()
    try:
        for index, (account_id, path) in enumerate(selected):
            if job.get("cancel"):
                break
            name = _read().get(account_id, {}).get("username") or "Instagram"
            item = {"account_id": account_id, "username": name, "video": path.name,
                    "status": "running", "message": ""}
            job["items"].append(item)
            job["progress"] = index
            try:
                _stage(item, "open_profile", "Подключаю профиль браузера")
                ctx = await _context(account_id)
                cover = (covers or {}).get(path.name)
                if cover is not None:
                    result = await _publish_via_browser(
                        ctx, path, caption, item=item, cover=cover,
                    )
                else:
                    result = await _publish_via_browser(ctx, path, caption, item=item)
                item["status"] = result
                item["message"] = ("✓ Опубликовано" if result == "published" else
                                   "Результат отправки не подтверждён. Проверь ролик "
                                   "в аккаунте; автоматического повтора нет.")
                key = path.resolve()
                if result != "published":
                    uncertain.add(key)
                remaining[key] -= 1
                if remaining[key] == 0 and key not in uncertain:
                    try:
                        destination = move_confirmed_video(path)
                        item["removed_from_queue"] = True
                        item["stored_at"] = str(destination)
                        item["message"] += " · Убрано из папки загрузки"
                    except (OSError, ValueError) as exc:
                        # Do not mark an Instagram publish as failed because
                        # cleanup was blocked by the filesystem.
                        item["cleanup_warning"] = str(exc)[:240]
            except Exception as exc:
                log.error("Browser IG upload error (account=%s, stage=%s): %s",
                          account_id[:8], item.get("step", "?"),
                          type(exc).__name__)
                item["status"] = "error"
                item["message"] = _brief_error(exc)
                uncertain.add(path.resolve())
            job["progress"] = index + 1
            # On an uncertain Share result, stop the queue to avoid duplicates.
            if item["status"] in ("needs_review", "error"):
                job["status"] = ("needs_review" if item["status"] == "needs_review" else "stopped")
                return
        job["status"] = "stopped" if job.get("cancel") else "completed"
    except asyncio.CancelledError:
        job["status"] = "stopped"
        raise
    finally:
        job["finished_at"] = int(time.time())


@router.post("/publish", status_code=202)
async def publish(body: PublishInput, request: Request):
    _local(request)
    global CURRENT_JOB, CURRENT_TASK
    if not body.confirmed:
        raise HTTPException(400, "Подтверди отправку выбранных видео")
    if CURRENT_TASK and not CURRENT_TASK.done():
        raise HTTPException(409, "Уже выполняется браузерная публикация")
    known = _read()
    account_ids = list(dict.fromkeys(body.accounts))
    names = list(dict.fromkeys(body.filenames))
    if not all(key in known for key in account_ids):
        raise HTTPException(400, "Выбранный Instagram-аккаунт отсутствует")
    try:
        clips = [video_path(body.folder_id, name) for name in names]
    except (ValueError, OSError) as exc:
        raise HTTPException(400, "Одно из видео не найдено в выбранной папке") from exc
    if not all(p.suffix.lower() in VIDEO_SUFFIXES for p in clips):
        raise HTTPException(400, "Для браузерной публикации выбери MP4/MOV")
    if body.require_covers and any(name not in body.covers for name in names):
        raise HTTPException(400, "Заранее выбери обложку для каждого отмеченного ролика")
    if any(name not in names for name in body.covers):
        raise HTTPException(400, "Список обложек не совпадает с выбранными видео")
    covers = {name: _cover_path(cover_id) for name, cover_id in body.covers.items()}
    # Deliberate explicit bounded snapshot. Each selected video is queued for
    # each selected account. No scheduled repeats or stealth automation.
    if len(clips) * len(account_ids) > 25:
        raise HTTPException(400, "Не более 25 публикаций за одно ручное задание")
    jobs = [(account_id, clip) for account_id in account_ids for clip in clips]
    CURRENT_JOB = {
        "id": secrets.token_hex(8), "status": "running",
        "created_at": int(time.time()), "finished_at": None,
        "accounts": account_ids, "total": len(jobs),
        "progress": 0, "items": [], "cancel": False,
    }
    if covers:
        CURRENT_TASK = asyncio.create_task(
            _run_job(CURRENT_JOB, jobs, body.caption, covers=covers)
        )
    else:
        CURRENT_TASK = asyncio.create_task(_run_job(CURRENT_JOB, jobs, body.caption))
    return {"ok": True, "job_id": CURRENT_JOB["id"], "total": len(jobs)}


@router.post("/publish/stop")
def cancel(request: Request):
    _local(request)
    if CURRENT_JOB and CURRENT_JOB["status"] == "running":
        CURRENT_JOB["cancel"] = True
    return {"ok": True}


async def shutdown_browser_instagram():
    global PLAYWRIGHT, CURRENT_TASK
    if CURRENT_TASK and not CURRENT_TASK.done():
        CURRENT_JOB["cancel"] = True
        CURRENT_TASK.cancel()
        try:
            await CURRENT_TASK
        except asyncio.CancelledError:
            pass
    for account_id in set(CONTEXTS) | set(CAMOUFOX_HANDLES):
        await close_account_context(account_id)
    if PLAYWRIGHT is not None:
        try:
            await PLAYWRIGHT.stop()
        except Exception:
            pass
        PLAYWRIGHT = None
