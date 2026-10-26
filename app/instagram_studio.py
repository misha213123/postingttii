"""Instagram owned-content importer inside Telegram Studio.

Public account access depends on Instagram and yt-dlp; failures are reported,
not silently bypassed. Does not automate logins or circumvent restrictions.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import math
import random
import re
import subprocess
import uuid
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel, Field, field_validator
from typing import Literal
from fastapi.responses import FileResponse
from app.config import settings
from app.video_folders import folder_path, account_folder, account_background
from app.telegram_studio import (
    BACKGROUNDS, backgrounds, duration, is_image_background, RenderDesign,
    current_banner, saved_design, banner_input_args, banner_overlay_filter,
    validate_video_banner, quality_filter_segment, h264_quality_args,
)
from io import BytesIO
from PIL import Image, UnidentifiedImageError

router = APIRouter(prefix="/api/telegram-studio/instagram", tags=["studio-instagram"])
ROOT = settings.data_dir / "instagram_studio"
ROOT.mkdir(parents=True, exist_ok=True)
CONFIG = ROOT / "sources.json"
DOWNLOAD_LOG = ROOT / "download_history.jsonl"
SESSION_FILE = ROOT / "instaloader.session"
VIDEO_BANNERS = ROOT / "banners"
VIDEO_BANNERS.mkdir(parents=True, exist_ok=True)
JOB = {"status": "idle", "done": 0, "total": 0, "errors": []}
LOCK = asyncio.Lock()
TASK = None

class Source(BaseModel):
    url: str

class Run(BaseModel):
    limit_per_account: int = 5
    output_folder: str = "default"
    target_slot: int = 0


class InstagramLayout(BaseModel):
    """Independent, persisted 9:16 composition for normal Instagram video."""
    video_x: int = Field(default=65, ge=0, le=1080)
    video_y: int = Field(default=110, ge=0, le=1920)
    video_width: int = Field(default=950, ge=240, le=1080)
    video_height: int = Field(default=1700, ge=300, le=1920)
    video_opacity: float = Field(default=1.0, ge=0.1, le=1.0)
    background_mode: Literal["shuffle", "single", "blur", "none"] = "shuffle"
    background_name: str = ""
    quality_preset: Literal["standard", "enhanced", "maximum"] = "enhanced"
    # None means a legacy profile still inherits the Telegram ad settings;
    # after editing this video template the two are completely independent.
    banner_enabled: bool | None = None
    banner_x: int | None = Field(default=None, ge=-2160, le=2160)
    banner_y: int | None = Field(default=None, ge=-3840, le=3840)
    banner_width: int | None = Field(default=None, ge=100, le=1080)

    @field_validator("banner_x", "banner_y", "banner_width", mode="before")
    @classmethod
    def round_banner_pixels(cls, value):
        # Dragging a banner across a scaled HTML preview produces fractional
        # pixels. FFmpeg geometry uses whole source-frame pixels.
        if isinstance(value, float) and math.isfinite(value):
            return round(value)
        return value


def video_banner_file() -> Path | None:
    name = load().get("banner_file", None)
    if name is None:
        # Migrate legacy ordinary-video renders without silently losing ad.
        return current_banner()
    if not name or not re.fullmatch(r"[a-f0-9]{32}\.(?:png|mp4|webm)", name):
        return None
    path = VIDEO_BANNERS / name
    return path if path.is_file() else None


def video_banner_design(layout: InstagramLayout | None = None) -> RenderDesign:
    layout = layout or current_layout()
    design = saved_design()
    updates = {}
    for key in ("banner_enabled", "banner_x", "banner_y", "banner_width"):
        value = getattr(layout, key)
        if value is not None:
            updates[key] = value
    updates["quality_preset"] = layout.quality_preset
    # Telegram resource-saving mode must not silently change the separate
    # ordinary-Instagram-video export profile (including its ad banner).
    updates["render_speed"] = "original"
    return design.model_copy(update=updates)


def banner_response() -> dict:
    path = video_banner_file()
    return {
        "available": path is not None,
        "kind": ("video" if path and path.suffix in {".mp4", ".webm"}
                 else "image" if path else None),
        "legacy": "banner_file" not in load(),
    }


@router.get("/banner")
def get_video_banner():
    return banner_response()


@router.get("/banner/preview")
def video_banner_preview():
    path = video_banner_file()
    if path is None:
        raise HTTPException(404, "Баннер обычных видео не загружен")
    types = {".png": "image/png", ".mp4": "video/mp4", ".webm": "video/webm"}
    return FileResponse(path, media_type=types[path.suffix],
                        headers={"Cache-Control": "no-store"})


@router.post("/banner")
async def upload_video_banner(file: UploadFile = File(...)):
    if JOB["status"] == "running":
        raise HTTPException(409, "Дождись окончания рендера Instagram-видео")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm"}:
        raise HTTPException(400, "Загрузи PNG, JPG, WebP, MP4 или WebM")
    image = suffix in {".png", ".jpg", ".jpeg", ".webp"}
    name = uuid.uuid4().hex + (".png" if image else suffix)
    path = VIDEO_BANNERS / name
    try:
        if image:
            raw = await file.read(10 * 1024 * 1024 + 1)
            if len(raw) > 10 * 1024 * 1024:
                raise HTTPException(413, "Изображение больше 10 МБ")
            try:
                with Image.open(BytesIO(raw)) as first:
                    first.verify()
                with Image.open(BytesIO(raw)) as original:
                    original.load()
                    if not (16 <= original.width <= 6000 and 16 <= original.height <= 6000):
                        raise ValueError("Неверные размеры изображения")
                    original.convert("RGBA").save(path, "PNG")
            except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
                raise HTTPException(400, "Некорректная фотография баннера")
        else:
            count = 0
            with path.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    count += len(chunk)
                    if count > 100 * 1024 * 1024:
                        raise HTTPException(413, "Видео-баннер больше 100 МБ")
                    output.write(chunk)
            validate_video_banner(path)
        state = load()
        state["banner_file"] = name
        state.setdefault("layout", {})["banner_enabled"] = True
        save(state)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return banner_response()


@router.delete("/banner")
def remove_video_banner():
    if JOB["status"] == "running":
        raise HTTPException(409, "Дождись окончания рендера Instagram-видео")
    state = load()
    state["banner_file"] = ""
    state.setdefault("layout", {})["banner_enabled"] = False
    save(state)
    return banner_response()


def current_layout() -> InstagramLayout:
    return InstagramLayout.model_validate(load().get("layout", {}))


@router.get("/layout")
def get_layout():
    layout = current_layout()
    effective = video_banner_design(layout)
    return {"layout": layout.model_dump(), "banner_design": {
        "banner_enabled": effective.banner_enabled, "banner_x": effective.banner_x,
        "banner_y": effective.banner_y, "banner_width": effective.banner_width,
    }, "banner": banner_response()}


@router.post("/layout")
def set_layout(body: InstagramLayout):
    if JOB["status"] == "running":
        raise HTTPException(409, "Дождись окончания Instagram-рендера")
    d = load()
    d["layout"] = body.model_dump()
    save(d)
    return {"layout": body.model_dump()}


@router.get("/sample/preview")
def sample_video_preview():
    cached = [p for p in ROOT.glob("ig_*.mp4") if not p.name.endswith(".rendering.mp4")]
    cached.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    if not cached:
        raise HTTPException(404, "Сначала скачай один Instagram-ролик")
    return FileResponse(cached[0], media_type="video/mp4")


def choose_instagram_background(layout: InstagramLayout, pool: list[Path]) -> Path | None:
    if layout.background_mode in ("blur", "none"):
        return None
    if layout.background_mode == "single":
        if not layout.background_name:
            raise ValueError("Выбери один фон для Instagram")
        chosen = BACKGROUNDS / layout.background_name
        if chosen not in pool:
            raise ValueError("Фон Instagram удалён или выключен")
        return chosen
    return None  # shuffle mode is selected from the rotated pool by process()

class LinkInput(BaseModel):
    url: str

def load():
    if not CONFIG.exists():
        # Keep sources entered in the newer Instagram Studio UI.
        previous = settings.data_dir / "telegram_studio" / "instagram_sources.json"
        if previous.exists():
            try:
                entries = json.loads(previous.read_text(encoding="utf-8"))
                return {"accounts": ["https://www.instagram.com/" + name + "/" for name in entries.get("accounts", [])],
                        "disabled": [], "processed": [], "hashes": [], "links": []}
            except (ValueError, OSError):
                pass
        return {"accounts": [], "disabled": [], "processed": [], "hashes": [], "links": []}
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    for key in ("accounts", "disabled", "processed", "hashes", "links"):
        data.setdefault(key, [])
    return data

def save(data):
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG)

def reel_id(url: str) -> str:
    """Identify a post regardless of /reel/ vs /p/ URL spelling."""
    match = re.search(r"/(?:reel|p|tv)/([A-Za-z0-9_-]+)", urlparse(url).path)
    return match.group(1) if match else url


def record_download(account: str, url: str, result: str, **extra) -> None:
    entry = {"at": datetime.now(timezone.utc).isoformat(),
             "account": account, "url": url, "result": result, **extra}
    with DOWNLOAD_LOG.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")


def normalize(url):
    url = url.strip()
    if not url.startswith("http"):
        url = "https://www.instagram.com/" + url.lstrip("@/")
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ("instagram.com", "www.instagram.com"):
        raise HTTPException(400, "Нужна ссылка на Instagram-профиль")
    parts = parsed.path.strip("/").split("/")
    if len(parts) != 1 or not re.fullmatch(r"[A-Za-z0-9_.]{1,30}", parts[0]):
        raise HTTPException(400, "Укажи ссылку на профиль, а не отдельный пост")
    return "https://www.instagram.com/" + parts[0] + "/"

@router.get("/status")
def status():
    d = load()
    return {"accounts": d["accounts"], "disabled": d["disabled"], "links": d["links"], "session_connected": SESSION_FILE.exists(), "job": JOB, "processed_count": len(d["processed"])}

def normalize_post(url):
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or parsed.hostname not in ("www.instagram.com", "instagram.com"):
        raise HTTPException(400, "Нужна прямая HTTPS-ссылка на Instagram-публикацию")
    match = re.fullmatch(r"/(p|reel|tv)/([A-Za-z0-9_-]+)/?", parsed.path)
    if not match:
        raise HTTPException(400, "Поддерживаются ссылки /p/, /reel/ и /tv/")
    return f"https://www.instagram.com/{match.group(1)}/{match.group(2)}/"

@router.post("/links")
def add_link(body: LinkInput):
    url = normalize_post(body.url)
    d = load()
    if url not in d["links"] and url not in d["processed"]:
        d["links"].append(url)
        save(d)
    return {"links": d["links"]}

@router.delete("/links/{shortcode}")
def delete_link(shortcode: str):
    d = load()
    d["links"] = [url for url in d["links"] if urlparse(url).path.rstrip("/").split("/")[-1] != shortcode]
    save(d)
    return {"links": d["links"]}

CHROME_PROFILE = ROOT / "chrome_instagram_profile"

def find_chrome():
    candidates = [shutil.which("chrome"), shutil.which("chrome.exe")]
    if os.name == "nt":
        for env, suffix in (("PROGRAMFILES", "Google/Chrome/Application/chrome.exe"),
                            ("PROGRAMFILES(X86)", "Google/Chrome/Application/chrome.exe"),
                            ("LOCALAPPDATA", "Google/Chrome/Application/chrome.exe")):
            if os.environ.get(env):
                candidates.append(str(Path(os.environ[env]) / suffix))
    return next((p for p in candidates if p and Path(p).is_file()), None)

@router.post("/chrome/open")
def open_chrome_instagram():
    if sys.platform != "win32":
        raise HTTPException(400, "Вход через Chrome доступен только в локальном приложении Windows")
    executable = find_chrome()
    if not executable:
        raise HTTPException(404, "Google Chrome не найден")
    CHROME_PROFILE.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.Popen([executable, f"--user-data-dir={CHROME_PROFILE.resolve()}",
                          "--new-window", "https://www.instagram.com/"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True)
    except OSError as exc:
        raise HTTPException(500, f"Не удалось открыть Chrome: {exc}") from exc
    return {"opened": True, "message": "Открылся отдельный профиль Chrome для PostingTTII. Войди в Instagram в этом окне."}

@router.post("/session")
async def import_session(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".session"):
        raise HTTPException(400, "Выбери файл Instaloader с расширением .session")
    content = await file.read(1024 * 1024 + 1)
    if not content or len(content) > 1024 * 1024:
        raise HTTPException(400, "Файл сессии пустой или слишком большой")
    try:
        import tempfile
        import instaloader
        with tempfile.NamedTemporaryFile(delete=False, dir=ROOT) as tmp:
            tmp.write(content)
            temp_path = Path(tmp.name)
        try:
            loader = instaloader.Instaloader(quiet=True)
            loader.load_session_from_file("session", str(temp_path))
            if not loader.test_login():
                raise ValueError("Instagram не подтвердил сессию")
            temp_path.replace(SESSION_FILE)
            if os.name != "nt":
                SESSION_FILE.chmod(0o600)
        finally:
            temp_path.unlink(missing_ok=True)
    except Exception as exc:
        raise HTTPException(400, f"Не удалось подключить сессию: {str(exc)[:250]}") from exc
    return {"connected": True}

@router.delete("/session")
def disconnect_session():
    SESSION_FILE.unlink(missing_ok=True)
    return {"connected": False}

@router.post("/accounts")
def add(body: Source):
    url = normalize(body.url)
    d = load()
    if url not in d["accounts"]:
        d["accounts"].append(url)
        save(d)
    return {"accounts": d["accounts"]}

class Toggle(BaseModel):
    enabled: bool

@router.post("/accounts/{username}/toggle")
def toggle(username: str, body: Toggle):
    url = normalize(username)
    d = load()
    if url not in d["accounts"]:
        raise HTTPException(404, "Аккаунт не найден")
    disabled = set(d["disabled"])
    if body.enabled:
        disabled.discard(url)
    else:
        disabled.add(url)
    d["disabled"] = sorted(disabled)
    save(d)
    return {"disabled": d["disabled"]}

@router.delete("/accounts/{username}")
def remove(username: str):
    url = normalize(username)
    d = load()
    d["accounts"] = [x for x in d["accounts"] if x != url]
    save(d)
    return {"accounts": d["accounts"]}

def cmd(args, timeout=300):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise RuntimeError("Не найден yt-dlp или FFmpeg. Установи зависимости и перезапусти приложение.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Истекло время ожидания Instagram/FFmpeg") from exc
    if result.returncode:
        details = (result.stderr or result.stdout or "").strip()
        lines = [line.strip() for line in details.splitlines() if line.strip()]
        error = next((line for line in reversed(lines) if "ERROR:" in line), lines[-1] if lines else "Неизвестная ошибка")
        raise RuntimeError(error[:600])
    return result

def download_reel(link: str, source: Path) -> None:
    """Try public download first, then the user's own signed-in Chrome profile."""
    base = ["yt-dlp", "--no-playlist", "--max-filesize", "500M",
            "-f", "bv*+ba/b", "--merge-output-format", "mp4",
            "-o", str(source)]
    try:
        cmd(base + [link], 900)
        return
    except RuntimeError as first_error:
        message = str(first_error).lower()
        access_error = any(term in message for term in (
            "empty media response", "isn't available to everyone",
            "login required", "sign in", "cookies", "not available to everyone"))
        if not access_error or not CHROME_PROFILE.is_dir():
            raise
        # The Playwright discovery browser is closed before downloading.
        # yt-dlp reads cookies locally; no credentials are sent to our server.
        try:
            cmd(base + ["--cookies-from-browser",
                        "chrome:" + str(CHROME_PROFILE.resolve()), link], 900)
        except RuntimeError as retry_error:
            raise RuntimeError(
                f"Обычное скачивание: {str(first_error)[:230]}; "
                f"повтор с Chrome: {str(retry_error)[:230]}"
            ) from retry_error


def render(source, target, background, layout: InstagramLayout | None = None,
           *, banner: Path | None = None,
           banner_design: RenderDesign | None = None):
    """Render a normal Instagram Reel using its own 9:16 composition preset."""
    layout = layout or InstagramLayout()
    seconds = duration(source)
    if seconds <= 0:
        raise ValueError("Видео без корректной длительности")
    w, h = layout.video_width, layout.video_height
    x = min(layout.video_x, 1080 - w)
    y = min(layout.video_y, 1920 - h)
    # Keep original aspect ratio and fill the draggable rectangle with a
    # transparent canvas, so preview and FFmpeg share the same coordinates.
    foreground = (
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"format=rgba,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black@0,"
        f"format=rgba,colorchannelmixer=aa={layout.video_opacity:.3f}[front];"
    )
    args = ["ffmpeg", "-y", "-i", str(source)]
    if layout.background_mode == "blur":
        background_filter = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=40:10[bg];"
        )
    elif layout.background_mode == "none":
        background_filter = "color=c=black:s=1080x1920:r=30[bg];"
    else:
        if not background:
            raise ValueError("Не выбран активный Instagram-фон")
        if is_image_background(background):
            args += ["-loop", "1", "-i", str(background)]
        else:
            offset = random.uniform(0, max(0, duration(background) - seconds))
            args += ["-stream_loop", "-1", "-ss", str(offset), "-i", str(background)]
        background_filter = (
            "[1:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,setsar=1[bg];"
        )
    if banner_design is not None and banner_design.banner_enabled:
        if banner is None or not banner.is_file():
            raise ValueError("Включён рекламный баннер, но файл не загружен")
        banner_input_index = 2 if layout.background_mode in ("single", "shuffle") else 1
        args += banner_input_args(banner)
        filt = (
            background_filter + foreground
            + f"[bg][front]overlay={x}:{y}:shortest=1[composite];"
            + banner_overlay_filter(banner_design, banner_input_index)
        )
    else:
        filt = (
            background_filter + foreground
            + f"[bg][front]overlay={x}:{y}:shortest=1,fps=30,"
            + quality_filter_segment(layout.quality_preset)
            + "format=yuv420p[v]"
        )
    args += [
        "-filter_complex", filt,
        "-map", "[v]", "-map", "0:a?", "-t", str(seconds),
        "-c:v", "libx264",
        *h264_quality_args(layout.quality_preset, standard_crf="19"),
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", str(target),
    ]
    try:
        cmd(args, timeout=3600)
    except RuntimeError as exc:
        # The enhancement is optional. If this FFmpeg build lacks one of its
        # filters, preserve the old working export and do not lose the Reel.
        error = str(exc)
        if layout.quality_preset != "standard" and any(
            clue in error for clue in (
                "No such filter", "Error initializing complex filters",
                "Error reinitializing filters",
            )
        ):
            JOB.setdefault("cleanup_warnings", []).append(
                "FFmpeg не поддерживает улучшение качества — "
                "повторный экспорт со стандартными настройками."
            )
            fallback = layout.model_copy(update={"quality_preset": "standard"})
            legacy_banner = (banner_design.model_copy(
                update={"quality_preset": "standard"}
            ) if banner_design is not None else None)
            return render(
                source, target, background, fallback,
                banner=banner, banner_design=legacy_banner,
            )
        raise


def discover_edge(account):
    """Read visible reel links from a signed-in Chrome profile; no private API."""
    from playwright.sync_api import sync_playwright
    if not CHROME_PROFILE.exists():
        raise RuntimeError("Сначала войди в Instagram через кнопку Chrome")
    username = urlparse(account).path.strip("/")
    with sync_playwright() as playwright:
        try:
            context = playwright.chromium.launch_persistent_context(
                str(CHROME_PROFILE.resolve()), channel="chrome", headless=False,
                args=["--disable-extensions"], timeout=60000)
        except Exception as exc:
            raise RuntimeError("Не удалось открыть Chrome для сканирования. Закрой все окна Chrome с профилем PostingTTII и проверь, что Chrome не остался в фоновых процессах. Подробности: " + str(exc)[:450]) from exc
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(f"https://www.instagram.com/{username}/reels/", wait_until="domcontentloaded", timeout=45000)
            # Instagram may render a Reel grid before attaching its navigation links.
            # Collect both anchors and DOM URLs, then retry while scrolling.
            links = []
            seen_links = set()
            # Scroll down to older posts; the visible Instagram grid starts newest-first.
            for attempt in range(16):
                page.wait_for_timeout(2000)
                current = page.url
                if "/accounts/login" in current or "/challenge/" in current:
                    raise RuntimeError("Instagram требует вход или проверку. Открой Chrome PostingTTII и подтверди вход.")
                page_links = page.evaluate("""() => {
                    const urls = new Set();
                    const add = value => {
                        if (!value) return;
                        const text = String(value);
                        // Avoid regex literals: Instagram markup can contain escaped slashes.
                        for (const part of text.split(/["'\\s<>]+/)) {
                            const match = part.match(new RegExp('(?:https?://(?:www[.])?instagram[.]com)?/(?:reel|reels|p|tv)/[A-Za-z0-9_-]+/?'));
                            if (match) {
                                try { urls.add(new URL(match[0], location.origin).href); } catch (_) {}
                            }
                        }
                    };
                    for (const a of document.querySelectorAll('a[href]')) add(a.href);
                    for (const el of document.querySelectorAll('[href], [role="link"], [data-href]')) {
                        add(el.getAttribute('href'));
                        add(el.getAttribute('data-href'));
                    }
                    return Array.from(urls);
                }""")
                for candidate in page_links:
                    if candidate not in seen_links:
                        seen_links.add(candidate)
                        links.append(candidate)
                page.mouse.wheel(0, 1350)
            # Oldest among discovered posts first; dates are not exposed reliably by the grid.
            links.reverse()
            # Keep a snapshot of the DOM links to make future failures actionable.
            cleaned = []
            for link in links:
                try:
                    cleaned.append(normalize_post(link))
                except HTTPException:
                    continue
            if not cleaned:
                body = page.locator("body").inner_text(timeout=5000)[:3000].lower()
                if any(term in body for term in ("log in", "sign up", "войти", "зарегистрироваться")):
                    raise RuntimeError("Instagram показывает страницу входа вместо Reels. Войди через кнопку Chrome и закрой окно перед запуском.")
                if any(term in body for term in ("sorry, this page isn't available", "страница недоступна", "profile isn't available")):
                    raise RuntimeError(f"Instagram не открыл профиль @{username}. Проверь имя аккаунта и доступность профиля.")
                debug_image = ROOT / "instagram_discovery_debug.png"
                try:
                    page.screenshot(path=str(debug_image), full_page=False)
                except Exception:
                    pass
                actual_url = page.url
                title = page.title()[:100]
                raise RuntimeError(
                    f"У @{username} не найдены ссылки Reels. Открыта страница: {actual_url}; "
                    f"заголовок: {title}. Скриншот диагностики: {debug_image}. "
                    "Закрой отдельное окно Chrome перед запуском и проверь снимок."
                )
            return list(dict.fromkeys(cleaned))
        finally:
            context.close()


def discover_instaloader(account):
    import instaloader
    from itertools import islice
    username = urlparse(account).path.strip("/")
    loader = instaloader.Instaloader(quiet=True)
    if SESSION_FILE.exists():
        loader.load_session_from_file("session", str(SESSION_FILE))
    profile = instaloader.Profile.from_username(loader.context, username)
    posts = profile.get_posts()
    links = []
    for post in islice(posts, 150):
        if post.is_video:
            links.append(f"https://www.instagram.com/p/{post.shortcode}/")
    return links


def discover(account):
    result = cmd(["yt-dlp", "--flat-playlist", "--dump-single-json",
                  "--playlist-end", "100", account], timeout=120)
    data = json.loads(result.stdout)
    links = []
    for entry in data.get("entries") or []:
        if not entry:
            continue
        url = entry.get("webpage_url") or entry.get("url") or ""
        if not url.startswith("https://www.instagram.com/"):
            ident = entry.get("id", "")
            if not re.fullmatch(r"[A-Za-z0-9_-]{5,30}", ident):
                continue
            url = "https://www.instagram.com/reel/" + ident + "/"
        parsed = urlparse(url)
        if parsed.hostname in ("www.instagram.com", "instagram.com") and re.fullmatch(
            r"/(reel|p|tv)/[A-Za-z0-9_-]+/?", parsed.path
        ):
            links.append(url)
    return list(dict.fromkeys(links))

def is_profile_extraction_error(message):
    return "Unable to extract data" in message and "instagram:user" in message


def cleanup_instagram_download(source: Path, output: Path) -> bool:
    """Discard the downloaded original once a good render already exists.

    Never delete the finished output when the user selected the same directory
    as the download cache; keep the original after failed render attempts.
    """
    if source.resolve() == output.resolve():
        return False
    try:
        source.unlink(missing_ok=True)
        return True
    except OSError as exc:
        JOB.setdefault("cleanup_warnings", []).append(
            f"{source.name}: не удалось удалить скачанный оригинал: {exc}"
        )
        return False


async def process(limit, layout: InstagramLayout, *,
                  output_dir: Path | None = None, target_slot: int = 0,
                  banner: Path | None = None,
                  banner_design: RenderDesign | None = None):
    JOB.update(status="running", done=0, total=0, errors=[], cleanup_warnings=[])
    try:
        d = load()
        output_dir = output_dir or settings.upload_dir
        render_scope = ((str(target_slot) + "@" + str(output_dir.resolve())) if target_slot else
                        ("folder:" + str(output_dir.resolve())
                         if output_dir.resolve() != settings.upload_dir.resolve() else ""))
        if render_scope:
            known = set(d.setdefault("processed_by_target", {}).get(render_scope, []))
            hashes = set(d.setdefault("hashes_by_target", {}).get(render_scope, []))
        else:
            known = set(d["processed"])
            hashes = set(d["hashes"])
        known_ids = {reel_id(link) for link in known}
        # One layout snapshot and one rotating background queue for this job.
        pool = [BACKGROUNDS / bg["name"] for bg in backgrounds() if bg["enabled"]]
        if layout.background_mode == "single":
            choose_instagram_background(layout, pool)
        if layout.background_mode == "shuffle" and not pool:
            raise ValueError("Добавь хотя бы один активный фон или выбери размытие")
        background_queue: list[Path] = []
        previous_background: Path | None = None

        def next_background() -> Path | None:
            nonlocal previous_background
            if layout.background_mode != "shuffle":
                return choose_instagram_background(layout, pool)
            if not background_queue:
                background_queue.extend(random.sample(pool, len(pool)))
                if (previous_background is not None and len(background_queue) > 1
                        and background_queue[0] == previous_background):
                    background_queue[0], background_queue[1] = background_queue[1], background_queue[0]
            previous_background = background_queue.pop(0)
            return previous_background

        candidates = []
        for account in d["accounts"]:
            if account in d["disabled"]:
                continue
            try:
                if CHROME_PROFILE.exists():
                    links = await asyncio.to_thread(discover_edge, account)
                elif SESSION_FILE.exists():
                    links = await asyncio.to_thread(discover_instaloader, account)
                else:
                    raise RuntimeError("Сначала открой Instagram через Chrome и войди в аккаунт. Файл Instaloader не нужен.")
                candidates.extend((account, link) for link in links
                                  if reel_id(link) not in known_ids)
            except Exception as exc:
                JOB["errors"].append(f"{account}: " + str(exc)[:500])
        candidates.extend(("direct", link) for link in d["links"]
                          if reel_id(link) not in known_ids)
        # Randomize source accounts, not the chronological priority inside each source.
        pools = {}
        for account, link in candidates:
            pools.setdefault(account, []).append(link)
        selected = []
        selected_ids = set()
        counts = {}
        while any(pools.values()):
            active = [account for account, pool in pools.items()
                      if pool and (account == "direct" or counts.get(account, 0) < limit)]
            if not active:
                break
            random.shuffle(active)
            for account in active:
                while pools[account]:
                    link = pools[account].pop(0)
                    if reel_id(link) in selected_ids:
                        continue
                    selected.append((account, link))
                    selected_ids.add(reel_id(link))
                    counts[account] = counts.get(account, 0) + 1
                    break
        JOB["total"] = len(selected)
        for account, link in selected:
            stem = "ig_" + hashlib.sha256(link.encode()).hexdigest()[:18]
            source = ROOT / (stem + ".mp4")
            output = output_dir / (stem + ".mp4")
            temp = ROOT / (stem + ".rendering.mp4")
            try:
                if output.exists() and output.stat().st_size:
                    known.add(link)
                    known_ids.add(reel_id(link))
                    record_download(account, link, "already_in_queue", file=output.name)
                    cleanup_instagram_download(source, output)
                    continue
                if not source.exists():
                    await asyncio.to_thread(download_reel, link, source)
                if not source.exists() or duration(source) <= 0:
                    raise ValueError("Instagram не предоставил доступный MP4")
                digest = await asyncio.to_thread(lambda: hashlib.sha256(source.read_bytes()).hexdigest())
                if digest in hashes:
                    known.add(link)
                    known_ids.add(reel_id(link))
                    record_download(account, link, "duplicate_content", sha256=digest)
                    cleanup_instagram_download(source, output)
                    continue
                bg = next_background()
                await asyncio.to_thread(render, source, temp, bg, layout,
                                        banner=banner, banner_design=banner_design)
                if duration(temp) <= 0:
                    raise ValueError("Рендер не прошёл проверку")
                temp.replace(output)
                # Real MP4 is validated and moved into the render folder.
                # The downloaded Instagram original is no longer needed.
                cleanup_instagram_download(source, output)
                hashes.add(digest)
                known.add(link)
                known_ids.add(reel_id(link))
                record_download(account, link, "downloaded", file=output.name, sha256=digest)
                d["last_output"] = {
                    "name": output.name, "path": str(output.resolve()),
                    "updated_at": int(time.time()),
                    "banner_applied": bool(banner_design and banner_design.banner_enabled),
                }
                JOB["done"] += 1
            except Exception as exc:
                JOB["errors"].append(f"{link}: {str(exc)[:600]}")
                try:
                    record_download(account, link, "error", message=str(exc)[:300])
                except OSError:
                    pass
            finally:
                temp.unlink(missing_ok=True)
                if render_scope:
                    d.setdefault("processed_by_target", {})[render_scope] = sorted(known)
                    d.setdefault("hashes_by_target", {})[render_scope] = sorted(hashes)
                else:
                    d["processed"] = sorted(known)
                    d["links"] = [url for url in d["links"] if url not in known]
                    d["hashes"] = sorted(hashes)
                save(d)
        JOB["status"] = "done" if not JOB["errors"] else "partial"
    except Exception as exc:
        JOB["status"] = "error"
        JOB["errors"].append(str(exc)[:250])
    finally:
        LOCK.release()

@router.post("/run")
async def run(body: Run):
    global TASK
    if not 1 <= body.limit_per_account <= 100:
        raise HTTPException(400, "Лимит 1–100")
    if LOCK.locked():
        raise HTTPException(409, "Загрузка Instagram уже запущена")
    if not load()["accounts"] and not load()["links"]:
        raise HTTPException(400, "Добавь Instagram-аккаунт или ссылку на публикацию")
    layout = current_layout()
    try:
        if body.target_slot:
            selected_folder = (account_folder(body.target_slot) if body.output_folder == "linked"
                               else body.output_folder)
            selected_bg = account_background(body.target_slot, "instagram")
            if selected_bg:
                layout = layout.model_copy(update={
                    "background_mode": "single", "background_name": selected_bg,
                })
        else:
            if body.output_folder == "linked":
                raise ValueError("Выбери Instagram-аккаунт для привязанной папки")
            selected_folder = body.output_folder
        output_dir = folder_path(selected_folder, writable=True)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if layout.background_mode in ("shuffle", "single"):
        pool = [BACKGROUNDS / bg["name"] for bg in backgrounds() if bg["enabled"]]
        if layout.background_mode == "single":
            try:
                choose_instagram_background(layout, pool)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        elif not pool:
            # Remain backward compatible with importer runs before any MP4
            # backgrounds have been uploaded: fall back to blurred source.
            layout = layout.model_copy(update={"background_mode": "blur"})
    banner_design = video_banner_design(layout)
    banner = video_banner_file()
    if banner_design.banner_enabled and banner is None:
        raise HTTPException(400, "Включён рекламный баннер, но файл не загружен")
    await LOCK.acquire()
    TASK = asyncio.create_task(process(
        body.limit_per_account, layout,
        output_dir=output_dir, target_slot=body.target_slot,
        banner=banner, banner_design=banner_design,
    ))
    return {"started": True}
