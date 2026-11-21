"""Isolated Telegram Studio API: local Telegram media -> random backgrounds -> Reels."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from datetime import date
from io import BytesIO
from fractions import Fraction
from typing import Literal

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from PIL import Image, ImageOps, ImageDraw, ImageFilter, UnidentifiedImageError
from app.config import settings
from app.video_folders import folder_path, account_folder, account_background

router = APIRouter(prefix="/api/telegram-studio", tags=["telegram-studio"])
ROOT = settings.data_dir / "telegram_studio"
BACKGROUNDS = ROOT / "backgrounds"
ORIGINALS = ROOT / "originals"
REGULAR_ORIGINALS = ORIGINALS / "regular"
BANNERS = ROOT / "banners"
RENDER_PREVIEWS = ROOT / "render_previews"
ORBIT_ASSETS = ROOT / "orbit_assets"
CONFIG = ROOT / "config.json"
SESSION = ROOT / "telegram"
for p in (ROOT, BACKGROUNDS, ORIGINALS, REGULAR_ORIGINALS, BANNERS, RENDER_PREVIEWS, ORBIT_ASSETS):
    p.mkdir(parents=True, exist_ok=True)
LOCK = asyncio.Lock()
JOB = {"status": "idle", "total": 0, "done": 0, "errors": []}
JOB_TASK = None
STOP_REQUESTED = False
ACTIVE_RENDER_PROCESS: subprocess.Popen | None = None
ACTIVE_RENDER_LOCK = threading.Lock()


class RenderStopped(Exception):
    """A user requested to stop the current render and its FFmpeg process."""


def run_ffmpeg_render(command: list[str], *, cwd: str | None = None,
                      timeout: float | None = None) -> subprocess.CompletedProcess:
    """Run cancellable FFmpeg, tracking the actual child process.

    subprocess.run() hid the PID, so the old UI could not stop a render that
    was already spending minutes inside FFmpeg. No other FFmpeg tasks on the
    computer are touched.
    """
    global ACTIVE_RENDER_PROCESS
    if STOP_REQUESTED and JOB.get("status") == "running":
        raise RenderStopped()
    process = subprocess.Popen(
        command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    with ACTIVE_RENDER_LOCK:
        ACTIVE_RENDER_PROCESS = process
        if STOP_REQUESTED and JOB.get("status") == "running":
            process.terminate()
    try:
        try:
            output, errors = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            output, errors = process.communicate()
            raise subprocess.TimeoutExpired(
                command, timeout, output=output, stderr=errors
            ) from exc
    finally:
        with ACTIVE_RENDER_LOCK:
            if ACTIVE_RENDER_PROCESS is process:
                ACTIVE_RENDER_PROCESS = None
    if STOP_REQUESTED and JOB.get("status") == "running":
        raise RenderStopped()
    if process.returncode:
        raise subprocess.CalledProcessError(
            process.returncode, command, output=output, stderr=errors
        )
    return subprocess.CompletedProcess(command, process.returncode, output, errors)
BACKGROUND_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
BACKGROUND_SUFFIXES = BACKGROUND_IMAGE_SUFFIXES | {".mp4"}


def is_image_background(path: Path) -> bool:
    return path.suffix.lower() in BACKGROUND_IMAGE_SUFFIXES


def telegram_media_matches(message, media_mode: str) -> bool:
    """Only native Telegram round video notes are allowed in circles mode.

    Telethon usually exposes the .video_note shortcut. Some message wrappers
    expose only DocumentAttributeVideo(round_message=True), so check both.
    """
    document = getattr(message, "document", None)
    attrs = getattr(document, "attributes", ()) or ()
    round_attribute = any(
        bool(getattr(attribute, "round_message", False))
        for attribute in attrs
    )
    video_note = bool(getattr(message, "video_note", None)) or round_attribute
    video = bool(getattr(message, "video", None))
    mime = str(getattr(document, "mime_type", "") or "").lower()
    if media_mode == "circles":
        return video_note
    if media_mode == "videos":
        return not video_note and (video or mime.startswith("video/"))
    return False



def config():
    if not CONFIG.exists():
        return {"channels": [], "processed": [], "backgrounds_disabled": []}
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def rendered_hashes(data):
    # Rebuild from cached Telegram originals that have actually been rendered.
    # Includes older runs from before hash tracking was introduced.
    hashes = set(data.get("rendered_hashes", []))
    processed = set(data.get("processed", []))
    for source in ORIGINALS.glob("*.mp4"):
        stem = source.stem
        if "_" not in stem:
            continue
        channel, message_id = stem.rsplit("_", 1)
        key = f"{channel}:{message_id}"
        target = settings.upload_dir / f"tg_{channel}_{message_id}.mp4"
        if key in processed or (target.is_file() and target.stat().st_size > 0):
            try:
                hashes.add(file_digest(source))
            except OSError:
                pass
    return hashes


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
    return [{"name": p.name,
             "kind": "image" if is_image_background(p) else "video",
             "enabled": p.name not in data.get("backgrounds_disabled", []),
             "size_mb": round(p.stat().st_size / 1048576, 2)}
            for p in sorted(BACKGROUNDS.iterdir())
            if p.is_file() and p.suffix.lower() in BACKGROUND_SUFFIXES]


class ChannelInput(BaseModel):
    channel: str


class ToggleInput(BaseModel):
    enabled: bool


class LoginStart(BaseModel):
    phone: str


class LoginFinish(BaseModel):
    code: str
    password: str = ""


class RenderDesign(BaseModel):
    """Saved design and layout in 1080 x 1920 video pixels."""
    visual_template: Literal["classic", "orbit_dots", "orbit_arc", "pulse_ring", "double_orbit", "dashed_spin", "ripple", "comet_tail", "halo_breathe", "twin_comets", "starlight", "radar_sweep"] = "classic"
    circle_size: int = Field(default=1040, ge=400, le=1080)
    circle_x: int = Field(default=20, ge=0, le=1080)
    circle_y: int = Field(default=90, ge=0, le=1920)
    # Modern video-note frame and independently draggable playback indicators.
    circle_border_width: int = Field(default=9, ge=0, le=48)
    circle_border_color: str = Field(default="#ffffff", pattern=r"^#[0-9A-Fa-f]{6}$")
    progress_enabled: bool = True
    progress_style: Literal["classic", "thin", "capsule", "segmented", "glow", "dual", "pulse", "none"] = "classic"
    progress_x: int = Field(default=310, ge=0, le=1080)
    progress_y: int = Field(default=1190, ge=0, le=1920)
    progress_width: int = Field(default=460, ge=100, le=1080)
    progress_height: int = Field(default=9, ge=3, le=30)
    timecode_enabled: bool = True
    timecode_x: int = Field(default=445, ge=0, le=1080)
    timecode_y: int = Field(default=1220, ge=0, le=1920)
    timecode_font_size: int = Field(default=32, ge=18, le=64)
    output_fps: Literal[30, 60, 120] = 60
    source_max_fps: bool = True  # Match the highest native video FPS (up to 120).
    smooth_motion: bool = False
    quality_preset: Literal["standard", "enhanced", "maximum"] = "enhanced"
    performance_version: int = Field(default=2, ge=1)
    export_resolution: Literal[1080, 2160] = 1080
    denoise_strength: float = Field(default=1.0, ge=0.0, le=3.0)
    sharpening_strength: float = Field(default=0.5, ge=0.0, le=1.5)
    # Resource-saving mode is the default for both existing and new designs.
    # Users can explicitly restore the previous 60fps/full-filter renderer.
    render_speed: Literal["quiet", "balanced", "original"] = "quiet"
    background_mode: Literal["shuffle", "single"] = "shuffle"
    background_name: str = ""
    chromakey_enabled: bool = True
    chromakey_version: int = 1
    chromakey_mode: Literal["standard", "advanced"] = "advanced"
    chromakey_color: str = Field(default="#00ff00", pattern=r"^#[0-9A-Fa-f]{6}$")
    chromakey_similarity: float = Field(default=0.22, ge=0.01, le=1.0)
    chromakey_blend: float = Field(default=0.07, ge=0.0, le=1.0)
    banner_enabled: bool = False
    # Negative coordinates deliberately allow moving the ad outside the
    # final video canvas; FFmpeg clips the off-canvas area naturally.
    banner_x: int = Field(default=270, ge=-2160, le=2160)
    banner_y: int = Field(default=1550, ge=-3840, le=3840)
    banner_width: int = Field(default=540, ge=100, le=1080)


def _migrate_legacy_performance(raw: dict) -> dict:
    """Undo the old Maximum button's accidental optical-flow auto-enable.

    Explicitly saved settings from this release include performance_version=2
    and are never overridden, even if the user selects Original on purpose.
    """
    raw = dict(raw)
    if raw.get("quality_preset") == "maximum" and "performance_version" not in raw:
        raw.update(render_speed="balanced", smooth_motion=False,
                   source_max_fps=False)
    return raw


def _design_presets(data: dict) -> dict[str, dict]:
    """Preserve the existing classic design and store the new style separately."""
    original = _migrate_legacy_performance(data.get("render_design") or {})
    saved = dict(data.get("render_design_presets") or {})
    classic = RenderDesign.model_validate(_migrate_legacy_performance({
        **original, **_migrate_legacy_performance(saved.get("classic", {})),
        "visual_template": "classic"
    })).model_dump()
    presets = {"classic": classic}
    for style in ("orbit_dots", "orbit_arc", "pulse_ring", "double_orbit", "dashed_spin", "ripple",
                  "comet_tail", "halo_breathe", "twin_comets", "starlight", "radar_sweep"):
        style_saved = _migrate_legacy_performance(saved.get(style, {}))
        presets[style] = RenderDesign.model_validate({
            **classic, **style_saved, "visual_template": style,
            **({"timecode_enabled": False} if not saved.get(style) else {}),
        }).model_dump()
    return presets


def effective_design(data: dict) -> RenderDesign:
    """Migrate pre-chromakey saved banners once, preserving newer manual off choices."""
    raw = _migrate_legacy_performance(data.get("render_design") or {})
    if data.get("banner_file") and "chromakey_version" not in raw:
        raw["chromakey_enabled"] = True
    return RenderDesign.model_validate(raw)


def saved_design() -> RenderDesign:
    return effective_design(config())


def choose_backgrounds(design: RenderDesign) -> list[Path]:
    """Resolve enabled video and image backgrounds at render time."""
    active = [BACKGROUNDS / item["name"] for item in backgrounds() if item["enabled"]]
    if design.background_mode == "single":
        if not design.background_name:
            raise ValueError("Выбери один фон в Telegram Studio")
        selected = BACKGROUNDS / design.background_name
        if selected not in active:
            raise ValueError("Выбранный фон Telegram удалён или выключен")
        return [selected]
    if not active:
        raise ValueError("Добавь хотя бы один активный фон")
    return active


def banner_input_args(banner: Path) -> list[str]:
    """Loop image and video ad overlays to the end of the source media."""
    if banner.suffix.lower() in {".mp4", ".webm"}:
        return ["-stream_loop", "-1", "-i", str(banner)]
    return ["-loop", "1", "-i", str(banner)]


def quality_filter_segment(quality_preset: str, denoise_strength: float = 1.0,
                           sharpening_strength: float = 0.5) -> str:
    """Conservative picture cleanup; visual quality, not duplicate evasion.

    The treatment is applied once after the composition so all layers share
    the same encoding characteristics. Keep the standard preset filter-free.
    """
    if quality_preset == "standard":
        return ""
    # Respect the user's noise/detail tradeoff instead of forcing aggressive
    # smoothing. All values are Pydantic-validated before entering FFmpeg.
    filters = []
    if denoise_strength > 0:
        strength = denoise_strength * (1.2 if quality_preset == "maximum" else 0.8)
        filters.append(f"hqdn3d={strength:.2f}:{strength*.85:.2f}:"
                       f"{strength*3:.2f}:{strength*2.5:.2f}")
    if sharpening_strength > 0:
        filters.append(f"unsharp=5:5:{sharpening_strength:.2f}:5:5:0.0")
    return ",".join(filters) + ("," if filters else "")


def h264_quality_args(quality_preset: str, *, standard_crf: str = "18") -> list[str]:
    """H.264 settings suited to a high-fidelity 1080x1920 Reels master."""
    # CRF controls picture fidelity; "slow" mostly improves compression
    # efficiency and is extremely costly for short 60fps Reels on Windows.
    # Medium keeps the same CRF quality at the cost of somewhat larger files.
    if quality_preset == "maximum":
        # Do not constrain bitrate: a hard VBV ceiling can damage a detailed banner.
        return ["-preset", "medium", "-crf", "14"]
    if quality_preset == "enhanced":
        return ["-preset", "fast", "-crf", "17",
                "-maxrate", "14M", "-bufsize", "28M"]
    return ["-preset", "medium", "-crf", standard_crf]


def render_performance(design: RenderDesign) -> dict:
    """Cap CPU load without altering resolution, layout, audio or effects.

    Quiet is deliberately capped at 30fps and disables expensive optional
    sharpening/denoising and optical-flow interpolation, while maintaining
    1080x1920, the white circle, overlay, progress and banner.
    """
    # Maximum quality must not silently turn into standard 30 FPS in quiet mode.
    if design.quality_preset == "maximum":
        # Maximum describes picture quality, not an obligation to run
        # CPU-heavy optical flow at 1080p/120fps. Quiet/balanced are safe.
        if design.render_speed == "quiet":
            return dict(fps=30, motion=False, quality="maximum",
                        filter_threads=2, video_threads=2,
                        encoder=["-preset", "veryfast", "-crf", "17"])
        if design.render_speed == "balanced":
            return dict(fps=design.output_fps, motion=False, quality="maximum",
                        filter_threads=4, video_threads=4,
                        encoder=["-preset", "veryfast", "-crf", "16"])
        return dict(fps=design.output_fps, motion=design.smooth_motion,
                    quality="maximum", filter_threads=4, video_threads=4,
                    encoder=h264_quality_args("maximum"))
    if design.render_speed == "quiet":
        return dict(fps=30, motion=False, quality="standard",
                    filter_threads=2, video_threads=2,
                    encoder=["-preset", "veryfast", "-crf", "19"])
    if design.render_speed == "balanced":
        return dict(fps=design.output_fps, motion=False,
                    quality=design.quality_preset, filter_threads=4,
                    video_threads=4,
                    encoder=h264_quality_args(design.quality_preset))
    return dict(fps=design.output_fps, motion=design.smooth_motion,
                quality=design.quality_preset, filter_threads=None,
                video_threads=None,
                encoder=h264_quality_args(design.quality_preset))


def effective_chroma_similarity(design: RenderDesign) -> float:
    """Compression-safe RGB threshold; manual settings above the floor win."""
    return (max(0.22, design.chromakey_similarity)
            if design.chromakey_mode == "advanced" else design.chromakey_similarity)


def banner_overlay_filter(
    design: RenderDesign, banner_input_index: int, fps: int | None = None,
) -> str:
    """Key at original banner resolution, then scale and composite.

    Crucially there is no FPS conversion, denoising or sharpening on the
    banner input. FFmpeg's overlay synchronizes its native timestamps to
    the output timeline. This avoids discarding frames before keying and
    prevents the global cleanup from blurring banner text and logos.
    """
    fps = fps or render_performance(design)["fps"]
    banner_key = ""
    if design.chromakey_enabled:
        banner_key = (
            f"colorkey=0x{design.chromakey_color[1:]}:"
            f"{effective_chroma_similarity(design):.3f}:"
            f"{design.chromakey_blend:.3f},"
        )
        if design.chromakey_mode == "advanced":
            rgb = tuple(int(design.chromakey_color[i:i+2], 16)
                        for i in (1, 3, 5))
            # Despill removes reflected green/blue halos without touching
            # unrelated hues. For red/neutral keys only clean the matte.
            spill = ("green" if rgb[1] > rgb[0] * 1.15 and rgb[1] > rgb[2] * 1.15
                     else "blue" if rgb[2] > rgb[0] * 1.15 and rgb[2] > rgb[1] * 1.15
                     else None)
            if spill:
                banner_key += f"despill=type={spill}:mix=0.60:expand=0.08,"
            # Blur only alpha, not RGB. This softens jagged chroma edges
            # without degrading banner text, color, detail or frame timing.
            banner_key += "boxblur=lr=0:lp=0:cr=0:cp=0:ar=1:ap=1,"
    delayed = design.visual_template != "classic"
    fade_start = f"{SECOND_TEMPLATE_INTRO_START:.2f}" if delayed else "0"
    fade_duration = f"{SECOND_TEMPLATE_FADE_SECONDS:.2f}" if delayed else "0.30"
    # For 4K output, upscale the *base* first and overlay the banner at
    # its 4K size directly from the original pixels. Upscaling a finished
    # 1080p composite would unnecessarily destroy fine banner text.
    hi_res = design.export_resolution == 2160
    factor = 2 if hi_res else 1
    preface = ("[composite]scale=2160:3840:flags=lanczos[composite_hi];"
               if hi_res else "")
    base = "composite_hi" if hi_res else "composite"
    return (
        preface +
        f"[{banner_input_index}:v]format=rgba,{banner_key}"
        f"scale={design.banner_width * factor}:-1:flags=lanczos,format=rgba,"
        f"fade=t=in:st={fade_start}:d={fade_duration}:alpha=1[banner];"
        f"[{base}][banner]overlay={design.banner_x * factor}:"
        f"{design.banner_y * factor}:format=auto:shortest=1,fps={fps},"
        "format=yuv420p[v]"
    )


def video_frame_rate(path: Path) -> float | None:
    """Read native FPS without decoding or rewriting the original video."""
    if not path.is_file() or path.suffix.lower() not in {".mp4", ".webm", ".mov", ".mkv"}:
        return None
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=avg_frame_rate,r_frame_rate",
             "-of", "json", str(path)],
            capture_output=True, text=True, check=True, timeout=15,
        )
        streams = json.loads(result.stdout).get("streams", [])
        for key in ("avg_frame_rate", "r_frame_rate"):
            if streams and streams[0].get(key):
                try:
                    fps = float(Fraction(streams[0][key]))
                except (ValueError, ZeroDivisionError, TypeError):
                    continue  # WebM often reports avg_frame_rate=0/0.
                if 1 <= fps <= 480:
                    return fps
    except (OSError, ValueError, TypeError, ZeroDivisionError,
            subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass
    return None


def render_frame_rate(
    design: RenderDesign, performance: dict, source: Path, bg: Path,
    banner: Path | None,
) -> tuple[int, dict[str, float | None]]:
    """Avoid downsampling any native video layer when practical (max 120fps).

    A single CFR MP4 cannot retain separate per-layer FPS metadata. Each
    native banner frame is still used on its original timeline; if the
    composite FPS is higher, frames are held, not interpolated or altered.
    """
    native = {
        "circle": video_frame_rate(source),
        "background": None if is_image_background(bg) else video_frame_rate(bg),
        "banner": (video_frame_rate(banner) if banner and design.banner_enabled else None),
    }
    target = performance["fps"]
    if design.source_max_fps and not (
        design.quality_preset == "maximum" and design.render_speed == "quiet"
    ):
        considered = (native.values() if performance["quality"] == "maximum"
                      else (native["banner"],))
        valid = [rate for rate in considered if rate is not None]
        if valid:
            target = max(target, math.ceil(max(valid) - 0.01))
    if target > 120:
        JOB.setdefault("notes", []).append(
            "Частота исходника выше 120 FPS: экспорт ограничен 120 FPS "
            "ради совместимости MP4; оригиналы не изменяются."
        )
    return min(120, target), native


def current_banner(data=None) -> Path | None:
    d = data if data is not None else config()
    name = d.get("banner_file", "")
    if not isinstance(name, str) or not re.fullmatch(r"[a-f0-9]{32}[.](?:png|mp4|webm)", name):
        return None
    path = BANNERS / name
    return path if path.is_file() else None


def design_response():
    d = config()
    banner = current_banner(d)
    return {
        "design": effective_design(d).model_dump(),
        "presets": _design_presets(d),
        "banner_available": banner is not None,
        "banner_kind": "video" if banner and banner.suffix in {".mp4", ".webm"} else ("image" if banner else None),
    }


@router.get("/design")
def get_design():
    return design_response()


@router.post("/design")
def update_design(body: RenderDesign):
    if JOB["status"] == "running":
        raise HTTPException(409, "Дождись завершения рендера перед изменением оформления")
    d = config()
    # Snapshot existing settings before switching templates on an older config.
    presets = _design_presets(d)
    presets[body.visual_template] = body.model_dump()
    d["render_design_presets"] = presets
    d["render_design"] = body.model_dump()
    save(d)
    return design_response()


@router.get("/banner/preview")
def banner_preview():
    path = current_banner()
    if not path:
        raise HTTPException(404, "Баннер не загружен")
    media_type = {".png": "image/png", ".mp4": "video/mp4", ".webm": "video/webm"}[path.suffix]
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "no-store"})


def validate_video_banner(path: Path) -> None:
    """Reject uploads without a decodable video stream or reasonable dimensions."""
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_type,width,height:format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=20, check=True,
        )
        info = json.loads(probe.stdout)
        streams = info.get("streams", [])
        if not streams or streams[0].get("codec_type") != "video":
            raise ValueError("В файле нет видеодорожки")
        width, height = int(streams[0].get("width", 0)), int(streams[0].get("height", 0))
        if not (16 <= width <= 3840 and 16 <= height <= 3840):
            raise ValueError("Размер видео-баннера должен быть от 16 до 3840 пикселей")
        seconds = float(info.get("format", {}).get("duration", 0))
        if not (0 < seconds <= 600):
            raise ValueError("Продолжительность баннера должна быть от 0 до 600 секунд")
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired,
            ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(400, f"Некорректный видео-баннер или FFprobe недоступен: {exc}") from exc


@router.post("/banner")
async def upload_banner(file: UploadFile = File(...)):
    if JOB["status"] == "running":
        raise HTTPException(409, "Нельзя изменять баннер во время рендера")
    suffix = Path(file.filename or "").suffix.lower()
    allowed_images = {".png", ".jpg", ".jpeg", ".webp"}
    allowed_videos = {".mp4", ".webm"}
    if suffix not in allowed_images | allowed_videos:
        raise HTTPException(400, "Загрузи баннер PNG, JPG, WEBP, MP4 или WebM")

    if suffix in allowed_images:
        data = await file.read(10 * 1024 * 1024 + 1)
        if len(data) > 10 * 1024 * 1024:
            raise HTTPException(413, "Изображение больше 10 МБ")
        try:
            with Image.open(BytesIO(data)) as image:
                image.verify()
            with Image.open(BytesIO(data)) as original:
                original.load()
                if original.width < 16 or original.height < 16 or original.width > 6000 or original.height > 6000:
                    raise ValueError("Размер баннера должен быть от 16 до 6000 пикселей")
                picture = original.convert("RGBA")
        except (ValueError, UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise HTTPException(400, f"Не удалось прочитать изображение баннера: {exc}") from exc
        name = uuid.uuid4().hex + ".png"
        picture.save(BANNERS / name, "PNG")
    else:
        # Stream to disk to avoid keeping an entire video in application memory.
        name = uuid.uuid4().hex + suffix
        dest = BANNERS / name
        total = 0
        try:
            with dest.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > 100 * 1024 * 1024:
                        raise HTTPException(413, "Видео-баннер больше 100 МБ")
                    output.write(chunk)
            validate_video_banner(dest)
        except Exception:
            dest.unlink(missing_ok=True)
            raise

    d = config()
    d["banner_file"] = name
    design = effective_design(d)
    d["render_design"] = design.model_copy(update={
        "banner_enabled": True, "chromakey_enabled": True,
    }).model_dump()
    save(d)
    return design_response()


@router.delete("/banner")
def delete_banner():
    if JOB["status"] == "running":
        raise HTTPException(409, "Нельзя удалять баннер во время рендера")
    d = config()
    d.pop("banner_file", None)
    design = RenderDesign.model_validate(d.get("render_design", {}))
    d["render_design"] = design.model_copy(update={"banner_enabled": False}).model_dump()
    save(d)
    return design_response()


RenderMode = Literal["circles", "videos", "instagram"]


def latest_render_path(mode: RenderMode) -> tuple[Path | None, dict]:
    """Only serve a renderer-produced MP4, not any user-specified path."""
    prefixes = {"circles": "tg_", "videos": "tgvideo_", "instagram": "ig_"}
    if mode == "instagram":
        from app.instagram_studio import load as instagram_load
        entry = instagram_load().get("last_output") or {}
    else:
        entry = (config().get("last_outputs") or {}).get(mode) or {}
    value = entry.get("path", "")
    if value:
        path = Path(value).resolve()
        if path.suffix.lower() == ".mp4" and path.name.startswith(prefixes[mode]) and not path.name.endswith(".rendering.mp4"):
            # Publishing archives completed MP4s: continue previewing them
            # from posted/ after the file leaves the chosen source folder.
            possible = [path, path.parent / "posted" / path.name,
                        settings.posted_dir / path.name]
            for candidate in possible:
                if candidate.is_file():
                    return candidate, entry
    # Legacy runs predating the latest-render index may live in inbox or
    # already be archived to posted/ by the regular Meta publishing queue.
    search_dirs = (settings.upload_dir, settings.posted_dir)
    paths = sorted(
        (path for folder in search_dirs
         for path in folder.glob(prefixes[mode] + "*.mp4")
         if path.is_file() and not path.name.endswith(".rendering.mp4")),
        key=lambda path: path.stat().st_mtime_ns, reverse=True,
    )
    if paths:
        path = paths[0]
        return path, {
            "name": path.name, "updated_at": int(path.stat().st_mtime),
            "banner_applied": None,
        }
    return None, {}


@router.get("/last-render")
def last_render(mode: RenderMode):
    path, info = latest_render_path(mode)
    if path is None:
        return {"available": False, "mode": mode, "message": "Готового ролика пока нет"}
    stat = path.stat()
    return {
        "available": True, "mode": mode, "name": path.name,
        "updated_at": info.get("updated_at") or int(stat.st_mtime),
        "banner_applied": info.get("banner_applied"),
        "fingerprint": f"{stat.st_mtime_ns}-{stat.st_size}",
        "video_url": f"/api/telegram-studio/last-render/video?mode={mode}",
        "poster_url": f"/api/telegram-studio/last-render/poster?mode={mode}",
    }


@router.get("/last-render/video")
def last_render_video(mode: RenderMode):
    path, _ = latest_render_path(mode)
    if path is None:
        raise HTTPException(404, "Готового ролика пока нет")
    return FileResponse(path, media_type="video/mp4",
                        headers={"Cache-Control": "no-store"},
                        content_disposition_type="inline")


@router.get("/last-render/poster")
def last_render_poster(mode: RenderMode):
    path, _ = latest_render_path(mode)
    if path is None:
        raise HTTPException(404, "Готового ролика пока нет")
    stat = path.stat()
    fingerprint = hashlib.sha256(
        f"{path.resolve()}:{stat.st_mtime_ns}:{stat.st_size}".encode()
    ).hexdigest()[:18]
    thumb = RENDER_PREVIEWS / f"{mode}_{fingerprint}.jpg"
    if not thumb.is_file():
        temp = RENDER_PREVIEWS / f"{mode}_{fingerprint}.working.jpg"
        try:
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-ss", "0.5", "-i", str(path),
                 "-frames:v", "1", "-vf", "scale=360:-2",
                 "-q:v", "3", str(temp)],
                check=True, capture_output=True, timeout=45,
            )
            if not temp.is_file() or temp.stat().st_size == 0:
                raise ValueError("Пустой кадр")
            temp.replace(thumb)
            # Keep a single cached thumbnail per output mode.
            for old in RENDER_PREVIEWS.glob(f"{mode}_*.jpg"):
                if old != thumb:
                    old.unlink(missing_ok=True)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError):
            raise HTTPException(503, "Не удалось создать миниатюру из готового видео")
        finally:
            temp.unlink(missing_ok=True)
    return FileResponse(thumb, media_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})


@router.get("/sample-circle/preview")
def sample_circle_preview():
    # Real Telegram sample when available; no remote download is triggered.
    samples = sorted(ORIGINALS.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not samples:
        raise HTTPException(404, "Пока нет скачанных Telegram-кружков")
    return FileResponse(samples[0], media_type="video/mp4")


class RenderRequest(BaseModel):
    limit_per_channel: int = 30
    media_mode: Literal["circles", "videos"] = "circles"
    output_folder: str = "default"
    target_slot: int = 0
    order: str = "oldest"
    date_from: date | None = None
    date_to: date | None = None
    # Explicitly rebuild selected outputs after layout/chromakey changes.
    force_rerender: bool = False


@router.get("/status")
def status():
    d = config()
    return {"channels": d["channels"], "backgrounds": backgrounds(), "job": JOB,
            "telegram_configured": bool(os.getenv("TG_API_ID") and os.getenv("TG_API_HASH")),
            "telegram_session_exists": SESSION.with_suffix(".session").exists(),
            "ffmpeg_ready": bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))}


@router.post("/stop")
def stop_render():
    """Stop only this studio's active FFmpeg process, not other video jobs."""
    global STOP_REQUESTED
    if JOB.get("status") != "running":
        return {"stopping": False, "status": JOB.get("status")}
    STOP_REQUESTED = True
    JOB["stopping"] = True
    with ACTIVE_RENDER_LOCK:
        active = ACTIVE_RENDER_PROCESS
        if active is not None and active.poll() is None:
            active.terminate()
    return {"stopping": True, "status": "stopping"}


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
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in BACKGROUND_SUFFIXES:
        raise HTTPException(400, "Поддерживаются MP4, JPG, JPEG, PNG и WebP")
    image = suffix in BACKGROUND_IMAGE_SUFFIXES
    name = uuid.uuid4().hex[:10] + (".jpg" if image else ".mp4")
    dest = BACKGROUNDS / name
    source = BACKGROUNDS / (uuid.uuid4().hex + ".upload")
    size = 0
    limit = 25 * 1024 * 1024 if image else 1024 * 1024 * 1024
    try:
        with source.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, "Фон слишком большой (фото до 25 МБ, MP4 до 1 ГБ)")
                out.write(chunk)
        if image:
            try:
                with Image.open(source) as original:
                    original.verify()
                with Image.open(source) as original:
                    if original.width * original.height > 60_000_000:
                        raise HTTPException(413, "Слишком большое разрешение изображения")
                    photo = ImageOps.exif_transpose(original)
                    if photo.mode in ("RGBA", "LA") or (photo.mode == "P" and "transparency" in photo.info):
                        rgba = photo.convert("RGBA")
                        rgb = Image.new("RGB", rgba.size, "#101827")
                        rgb.paste(rgba, mask=rgba.getchannel("A"))
                        photo = rgb
                    else:
                        photo = photo.convert("RGB")
                    photo.save(dest, "JPEG", quality=92, optimize=True)
            except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
                raise HTTPException(400, "Повреждённое, слишком большое или неподдерживаемое изображение")
        else:
            source.replace(dest)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    finally:
        source.unlink(missing_ok=True)
    return {"name": name, "backgrounds": backgrounds()}


@router.delete("/backgrounds/{name}")
def remove_background(name: str):
    if Path(name).name != name or Path(name).suffix.lower() not in BACKGROUND_SUFFIXES:
        raise HTTPException(400, "Некорректный файл")
    (BACKGROUNDS / name).unlink(missing_ok=True)
    d = config()
    d["backgrounds_disabled"] = [x for x in d["backgrounds_disabled"] if x != name]
    save(d)
    return {"backgrounds": backgrounds()}


@router.post("/backgrounds/{name}/toggle")
def toggle_background(name: str, body: ToggleInput):
    if Path(name).name != name or Path(name).suffix.lower() not in BACKGROUND_SUFFIXES:
        raise HTTPException(400, "Некорректное имя фона")
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
    if (Path(name).name != name or Path(name).suffix.lower() not in BACKGROUND_SUFFIXES
            or not (BACKGROUNDS / name).is_file()):
        raise HTTPException(404, "Фон не найден")
    suffix = Path(name).suffix.lower()
    return FileResponse(BACKGROUNDS / name, media_type={
        ".mp4": "video/mp4", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
    }[suffix])


def telegram_client():
    try:
        from telethon import TelegramClient
    except ImportError:
        raise HTTPException(503, "Установи telethon из requirements.txt")
    api_id, api_hash = os.getenv("TG_API_ID"), os.getenv("TG_API_HASH")
    if not api_id or not api_hash:
        raise HTTPException(400, "Заполни TG_API_ID и TG_API_HASH в .env")
    return TelegramClient(str(SESSION), int(api_id), api_hash)


from contextlib import asynccontextmanager


@asynccontextmanager
async def connected_telegram_client():
    """Connect without Telethon's interactive start()/console login."""
    client = telegram_client()
    await client.connect()
    try:
        yield client
    finally:
        await client.disconnect()


@router.get("/auth")
async def auth_status():
    async with connected_telegram_client() as client:
        return {"authorized": await client.is_user_authorized()}


@router.post("/auth/start")
async def auth_start(body: LoginStart):
    async with connected_telegram_client() as client:
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
    async with connected_telegram_client() as client:
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


# QR sign-in is held in memory while the browser polls for completion.
# Only the local application should expose these endpoints.
QR_STATE = {"client": None, "task": None, "qr": None, "state": "idle", "error": ""}
QR_LOCK = asyncio.Lock()


async def qr_cleanup():
    task = QR_STATE.get("task")
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    client = QR_STATE.get("client")
    if client:
        await client.disconnect()
    QR_STATE.update(client=None, task=None, qr=None)


async def qr_waiter(qr):
    from telethon.errors import SessionPasswordNeededError
    try:
        await qr.wait(timeout=55)
        QR_STATE["state"] = "authorized"
    except SessionPasswordNeededError:
        QR_STATE["state"] = "password_required"
    except asyncio.TimeoutError:
        QR_STATE["state"] = "expired"
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        QR_STATE["state"] = "error"
        QR_STATE["error"] = str(exc)[:180]


@router.post("/auth/qr/start")
async def qr_start():
    import base64
    import io
    import qrcode
    async with QR_LOCK:
        await qr_cleanup()
        client = telegram_client()
        try:
            await client.connect()
            if await client.is_user_authorized():
                await client.disconnect()
                QR_STATE["state"] = "authorized"
                return {"state": "authorized"}
            qr = await client.qr_login()
            image = qrcode.make(qr.url)
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            QR_STATE.update(client=client, qr=qr, state="waiting", error="")
            QR_STATE["task"] = asyncio.create_task(qr_waiter(qr))
            return {"state": "waiting", "image": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()}
        except Exception:
            await client.disconnect()
            raise


@router.get("/auth/qr/status")
async def qr_status():
    state = QR_STATE["state"]
    if state == "authorized" and QR_STATE.get("client"):
        async with QR_LOCK:
            if QR_STATE.get("client"):
                await qr_cleanup()
    return {"state": state, "error": QR_STATE["error"]}


class QRPassword(BaseModel):
    password: str


@router.post("/auth/qr/password")
async def qr_password(body: QRPassword):
    from telethon.errors import PasswordHashInvalidError
    async with QR_LOCK:
        if QR_STATE["state"] != "password_required" or not QR_STATE["client"]:
            raise HTTPException(409, "Сначала отсканируй QR-код")
        try:
            await QR_STATE["client"].sign_in(password=body.password)
        except PasswordHashInvalidError:
            raise HTTPException(400, "Неверный пароль 2FA")
        QR_STATE["state"] = "authorized"
        await qr_cleanup()
        return {"state": "authorized"}


def should_skip_existing_render(
    key: str, target: Path, completed_keys: list[str], *,
    force_rerender: bool = False,
) -> bool:
    """Skip already processed outputs unless the user explicitly requests a rebuild."""
    if force_rerender:
        return False
    if key in completed_keys:
        return True
    return target.is_file() and target.stat().st_size > 0


def should_skip_known_source(
    digest: str, known_hashes: set[str], *, force_rerender: bool = False,
) -> bool:
    """Avoid duplicate forwarded media unless explicitly rebuilding existing renders."""
    return not force_rerender and digest in known_hashes


def duration(path):
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                       capture_output=True, text=True, check=True)
    return float(p.stdout.strip())


def _is_windows() -> bool:
    return os.name == "nt"


def _timecode_fontfile() -> str | None:
    """Use an explicit Windows system font without relying on Fontconfig.

    Returns None on non-Windows systems (use normal drawtext discovery), a
    local basename when Windows provides a font, and "" when none is available.
    FFmpeg runs from ROOT so the basename contains no drive colon or escapes.
    """
    if not _is_windows():
        return None
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for name in ("arial.ttf", "segoeui.ttf", "tahoma.ttf", "verdana.ttf"):
        installed = fonts / name
        if not installed.is_file():
            continue
        cached = ROOT / "_timecode_render_font.ttf"
        try:
            if not cached.is_file() or cached.stat().st_size == 0:
                shutil.copyfile(installed, cached)
            return cached.name
        except OSError:
            continue
    return ""


def orbit_sprite_path(circle_size: int) -> Path:
    """Cache a clean WHITE moving highlight, without neon or particles.

    The first template's circular white border remains untouched. A short
    white arc circles it as a subtle animation in the second template.
    """
    filename = ORBIT_ASSETS / f"white_sweep_v3_{circle_size}.png"
    if filename.is_file() and filename.stat().st_size > 0:
        return filename
    dimension = 512
    padding = 36
    unit = dimension / (circle_size + 2 * padding)
    center = dimension / 2
    outer = (circle_size / 2 + 16) * unit
    segments = Image.new("RGBA", (dimension, dimension), (0, 0, 0, 0))
    draw = ImageDraw.Draw(segments)
    bounds = (center - outer, center - outer,
              center + outer, center + outer)
    draw.arc(bounds, 235, 288, fill=(255, 255, 255, 255),
             width=max(3, round(11 * unit)))
    image = segments
    filename.parent.mkdir(parents=True, exist_ok=True)
    temp = filename.with_name(filename.stem + "_" + uuid.uuid4().hex + ".png")
    try:
        image.save(temp, "PNG")
        temp.replace(filename)
    finally:
        temp.unlink(missing_ok=True)
    return filename


def circle_alpha_assets(size: int, border: int, color: str) -> tuple[Path, Path]:
    """Cache anti-aliased PNG circle masks instead of evaluating geq per pixel.

    Old rendering evaluated two expensive per-pixel geq expressions on every
    frame (at 1040px/60fps, millions of expressions each second). The masks
    are static for a design and can be reused across every frame and video.
    """
    inside = size - 2 * border
    mask_path = ORBIT_ASSETS / f"circle_inside_mask_v1_{inside}.png"
    ring_path = ORBIT_ASSETS / (
        f"circle_border_v1_{size}_{border}_{color.lstrip('#').lower()}.png"
    )

    def cached(path: Path) -> bool:
        try:
            return path.stat().st_size > 0
        except OSError:
            return False

    def save_once(path: Path, image: Image.Image) -> None:
        if cached(path):
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.stem + "_" + uuid.uuid4().hex + ".png")
        try:
            image.save(temporary, "PNG")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    if not cached(mask_path):
        factor = 2
        mask = Image.new("L", (inside * factor, inside * factor), 0)
        ImageDraw.Draw(mask).ellipse(
            (0, 0, inside * factor - 1, inside * factor - 1), fill=255
        )
        save_once(mask_path, mask.resize((inside, inside), Image.Resampling.LANCZOS))

    if not cached(ring_path):
        factor = 2
        ring = Image.new("RGBA", (size * factor, size * factor), (0, 0, 0, 0))
        rgb = tuple(int(color[i:i+2], 16) for i in (1, 3, 5))
        ImageDraw.Draw(ring).ellipse(
            (0, 0, size * factor - 1, size * factor - 1), fill=(*rgb, 255)
        )
        save_once(ring_path, ring.resize((size, size), Image.Resampling.LANCZOS))
    return mask_path, ring_path


def ring_sprite_path(circle_size: int, style: str) -> Path:
    """Create transparent ring artwork for each selectable animated template."""
    if style == "orbit_dots":
        return orbit_sprite_path(circle_size)
    filename = ORBIT_ASSETS / f"ring_{style}_v1_{circle_size}.png"
    if filename.is_file() and filename.stat().st_size:
        return filename
    dimension, padding = 512, 36
    unit = dimension / (circle_size + 2 * padding)
    center = dimension / 2
    radius = (circle_size / 2 + 16) * unit
    image = Image.new("RGBA", (dimension, dimension), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    def arc(r, start, end, color, width):
        draw.arc((center-r, center-r, center+r, center+r),
                 start, end, fill=color, width=max(2, int(width*unit)))
    if style == "orbit_arc":
        arc(radius, 205, 325, (255,255,255,255), 12)
        arc(radius+10*unit, 230, 300, (140,215,255,160), 4)
    elif style == "pulse_ring":
        arc(radius, 0, 359, (255,255,255,230), 7)
        arc(radius+13*unit, 0, 359, (180,225,255,120), 4)
    elif style == "double_orbit":
        arc(radius, 10, 115, (255,255,255,255), 9)
        arc(radius, 190, 295, (255,255,255,220), 9)
    elif style == "dashed_spin":
        for angle in range(0, 360, 30):
            arc(radius, angle, angle+16, (255,255,255,240), 9)
    elif style == "ripple":
        for offset, opacity in ((0,240),(12,145),(23,75)):
            arc(radius+offset*unit, 0, 359, (255,255,255,opacity), 5)
    elif style == "comet_tail":
        for start, end, opacity, width in ((190, 310, 65, 15),
                                          (245, 320, 135, 12),
                                          (290, 335, 240, 10)):
            arc(radius, start, end, (255, 255, 255, opacity), width)
        angle = math.radians(335)
        hx, hy = center + radius*math.cos(angle), center + radius*math.sin(angle)
        draw.ellipse((hx-7, hy-7, hx+7, hy+7), fill=(255, 255, 255, 255))
    elif style == "halo_breathe":
        for offset, opacity, width in ((0, 250, 8), (13, 150, 6), (24, 75, 5)):
            arc(radius+offset*unit, 0, 359, (195, 235, 255, opacity), width)
    elif style == "twin_comets":
        for start, end in ((15, 115), (195, 295)):
            arc(radius, start, end, (255, 255, 255, 245), 10)
            angle = math.radians(end)
            hx, hy = center+radius*math.cos(angle), center+radius*math.sin(angle)
            draw.ellipse((hx-6, hy-6, hx+6, hy+6), fill=(140, 225, 255, 255))
    elif style == "starlight":
        arc(radius, 0, 359, (255, 255, 255, 85), 3)
        for index in range(16):
            angle = 2*math.pi*index/16
            rr = radius + (8*unit if index % 2 else 0)
            hx, hy = center+rr*math.cos(angle), center+rr*math.sin(angle)
            dot = max(2, round((7 if index % 4 == 0 else 4)*unit))
            draw.ellipse((hx-dot, hy-dot, hx+dot, hy+dot),
                         fill=(255, 255, 255, 255 if index % 4 == 0 else 170))
    elif style == "radar_sweep":
        for start, end, opacity in ((175, 225, 60), (226, 275, 130),
                                    (276, 325, 230)):
            arc(radius, start, end, (155, 235, 255, opacity), 12)
        arc(radius, 320, 335, (255, 255, 255, 255), 14)
    else:
        raise ValueError(f"Неизвестный стиль кружка: {style}")
    temp = filename.with_name(filename.stem+"_"+uuid.uuid4().hex+".png")
    try:
        image.save(temp)
        temp.replace(filename)
    finally:
        temp.unlink(missing_ok=True)
    return filename


# A brief backdrop-only introduction before circles/ads reveal.
SECOND_TEMPLATE_INTRO_START = 0.38
SECOND_TEMPLATE_FADE_SECONDS = 0.28


def loading_progress_filter(
    design: RenderDesign, seconds: float, fps: int, previous: str
) -> str:
    """The same simple white loading bar as the classic template, no time.

    Track and white fill are invisible during the backdrop-only introduction.
    """
    w, h = design.progress_width, design.progress_height
    x = max(0, min(design.progress_x, 1080 - w))
    y = max(0, min(design.progress_y, 1920 - h))
    start = SECOND_TEMPLATE_INTRO_START
    return (
        f"[{previous}]drawbox=x={x}:y={y}:w={w}:h={h}:"
        f"color=white@0.34:t=fill:enable='gte(t,{start})'[loading_track];"
        f"color=c=white:s={w}x{h}:r={fps}:d={seconds:.3f},format=rgba,"
        "geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
        f"a='if(lte(X\\,W*min(T/{seconds:.3f}\\,1))\\,255\\,0)',"
        f"fade=t=in:st={start}:d={SECOND_TEMPLATE_FADE_SECONDS}:alpha=1"
        "[loading_fill];"
        f"[loading_track][loading_fill]overlay={x}:{y}:shortest=1:"
        f"enable='gte(t,{start})'[with_progress];"
    )


def styled_progress_filter(
    design: RenderDesign, seconds: float, fps: int, previous: str, *, orbit: bool
) -> str:
    """Render alternative animated progress bars at video resolution.

    This is deliberately separate from the classic filter so old saved
    designs keep their existing appearance and timecode behavior.
    """
    w, original_h = design.progress_width, design.progress_height
    h = min(4, original_h) if design.progress_style == "thin" else original_h
    x = max(0, min(design.progress_x, 1080 - w))
    y = max(0, min(design.progress_y + (original_h-h)//2, 1920-h))
    style = design.progress_style
    mask = "1"
    if style == "capsule":
        # Signed distance to a horizontal capsule. Escaped commas are needed
        # inside FFmpeg geq expressions embedded in a filtergraph.
        mask = ("lte((X-clip(X\\,H/2\\,W-H/2))*(X-clip(X\\,H/2\\,W-H/2))"
                "+(Y-H/2)*(Y-H/2)\\,(H/2)*(H/2))")
    elif style == "segmented":
        cell = max(12, w // 14)
        mask = f"lt(mod(X\\,{cell})\\,{max(6, cell-5)})"
    elif style == "dual":
        mask = "gte(abs(Y-H/2)\\,1.5)"
    progress = f"lte(X\\,W*min(T/{seconds:.3f}\\,1))"
    if style == "segmented":
        progress = f"lte(X\\,W*ceil(min(T/{seconds:.3f}\\,1)*14)/14)"
    opacity = "(0.72+0.28*sin(T*6))" if style == "pulse" else "1"
    fill_color = "0x6deeff" if style in {"glow", "dual"} else "white"
    track_opacity = "0.22" if style == "thin" else "0.34"
    track_label = "loading_track" if orbit else "progress_track"
    fill_label = "loading_fill" if orbit else "progress_fill"
    intro = SECOND_TEMPLATE_INTRO_START
    enabled = f":enable='gte(t,{intro})'" if orbit else ""
    fade = (f",fade=t=in:st={intro}:d={SECOND_TEMPLATE_FADE_SECONDS}:alpha=1"
            if orbit else "")
    return (
        f"color=c=white:s={w}x{h}:r={fps}:d={seconds:.3f},format=rgba,"
        "geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
        f"a='255*({mask})*{track_opacity}'[styled_track];"
        f"[{previous}][styled_track]overlay={x}:{y}:shortest=1"
        f"{enabled}[{track_label}];"
        f"color=c={fill_color}:s={w}x{h}:r={fps}:d={seconds:.3f},format=rgba,"
        "geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
        f"a='255*({mask})*({progress})*{opacity}'{fade}[{fill_label}];"
        f"[{track_label}][{fill_label}]overlay={x}:{y}:shortest=1"
        f"{enabled}[with_progress];"
    )


def render(
    source: Path,
    bg: Path,
    target: Path,
    design: RenderDesign | None = None,
    banner: Path | None = None,
    *,
    _fallbacks: frozenset[str] = frozenset(),
):
    """Composite a 9:16 Reel; preserve template on recoverable FFmpeg failures."""
    design = design or RenderDesign()
    seconds = duration(source)
    if seconds <= 0:
        raise ValueError("Некорректная длительность исходника")
    if is_image_background(bg):
        bg_input = ["-loop", "1", "-i", str(bg)]
    else:
        bg_seconds = duration(bg)
        if bg_seconds <= 0:
            raise ValueError("Некорректная длительность фона")
        offset = random.uniform(0, max(0, bg_seconds - seconds))
        bg_input = ["-stream_loop", "-1", "-ss", str(offset), "-i", str(bg)]

    size = design.circle_size
    border = min(design.circle_border_width, size // 5)
    inside = size - 2 * border
    mask_asset, border_asset = circle_alpha_assets(
        size, border, design.circle_border_color
    )
    x = max(0, min(design.circle_x, 1080 - size))
    y = max(0, min(design.circle_y, 1920 - size))
    performance = render_performance(design)
    if design.banner_enabled and (not banner or not banner.is_file()):
        raise ValueError("Включён рекламный баннер, но файл не загружен")
    fps, native = render_frame_rate(design, performance, source, bg, banner)

    # Optical flow is applied only when increasing FPS. Do not interpolate
    # an already high-frame-rate circle, and never interpolate banner frames.
    def motion_filter(native_fps: float | None) -> str:
        if (performance["motion"] and fps >= 60 and
                (native_fps is None or native_fps < fps - 0.5)):
            # The explicitly selected "original" speed mode can synthesize
            # circle frames. A blend is much cheaper than CPU optical flow;
            # full-frame background interpolation is always disabled.
            return f"minterpolate=fps={min(fps, 60)}:mi_mode=blend,fps={fps},"
        return f"fps={fps},"

    motion = motion_filter(native["circle"])
    # Never optical-flow interpolate a full 1080x1920 video background.
    # It multiplies work by millions of pixels per frame and can turn
    # a short Telegram circle into a 30-minute CPU render.
    background_motion = f"fps={fps},"
    source_cleanup = quality_filter_segment(
        performance["quality"], design.denoise_strength, design.sharpening_strength
    )
    # Keep the existing input positions: 0=source, 1=background,
    # 2=orbit (when enabled), next=banner (when enabled).
    # Add cached alpha mask and border PNGs after those inputs.
    orbit = design.visual_template != "classic"
    mask_index = 2 + int(orbit) + int(design.banner_enabled)
    border_index = mask_index + 1
    circle_filter = (
        f"[0:v]scale={size}:{size}:force_original_aspect_ratio=increase:"
        f"flags=lanczos,crop={inside}:{inside}:{border}:{border},"
        f"{source_cleanup}{motion}format=rgba[inside_raw];"
        f"[{mask_index}:v]format=gray[inside_mask];"
        "[inside_raw][inside_mask]alphamerge[inside];"
        f"[{border_index}:v]format=rgba[ring];"
        f"[ring][inside]overlay={border}:{border}:shortest=1:format=auto[circle];"
    )
    bg_filter = (
        "[1:v]scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop=1080:1920,{background_motion}setsar=1[bg];"
    )
    if orbit:
        # No circle or outline in the first 0.38s: only the background.
        circle_filter += (
            f"[circle]fade=t=in:st={SECOND_TEMPLATE_INTRO_START}:"
            f"d={SECOND_TEMPLATE_FADE_SECONDS}:alpha=1[circle_intro];"
        )
    circle_layer = "circle_intro" if orbit else "circle"
    filter_chain = (
        circle_filter + bg_filter +
        f"[bg][{circle_layer}]overlay={x}:{y}:shortest=1[base_circle];"
    )
    last = "base_circle"
    orbit_asset = ring_sprite_path(size, design.visual_template) if orbit else None
    if orbit:
        pad = 36
        style = design.visual_template
        speed = {"orbit_dots": 6, "orbit_arc": 3, "pulse_ring": 12,
                 "double_orbit": 5, "dashed_spin": 8, "ripple": 10,
                 "comet_tail": 4, "halo_breathe": 15,
                 "twin_comets": 6, "starlight": 9, "radar_sweep": 5}[style]
        # Pulse/ripple grow and shrink around the circle; other styles rotate.
        pulse = style in {"pulse_ring", "ripple", "halo_breathe"}
        base_dimension = size + 2 * pad
        # Animate opacity instead of changing dimensions: FFmpeg fade/overlay
        # require constant frame size, otherwise pulse renders fail on Windows.
        pulse_alpha = (
            ",geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
            "a='alpha(X,Y)*(0.7+0.3*sin(T*5))'"
            if pulse else ""
        )
        filter_chain += (
            f"[2:v]format=rgba,rotate=a='2*PI*t/{speed}':ow=iw:oh=ih:c=none"
            f"{pulse_alpha},scale={base_dimension}:{base_dimension}:flags=bilinear,"
            f"fade=t=in:st={SECOND_TEMPLATE_INTRO_START}:"
            f"d={SECOND_TEMPLATE_FADE_SECONDS}:alpha=1[orbit_ring];"
            f"[{last}][orbit_ring]overlay=x='{x+size//2}-w/2':"
            f"y='{y+size//2}-h/2':eval=frame:"
            "shortest=1:format=auto[with_orbit];"
        )
        last = "with_orbit"

    if design.progress_enabled and design.progress_style != "none" and orbit:
        filter_chain += (loading_progress_filter(design, seconds, fps, last)
                         if design.progress_style == "classic" else
                         styled_progress_filter(design, seconds, fps, last, orbit=True))
        last = "with_progress"
    elif design.progress_enabled and design.progress_style != "none":
        if design.progress_style != "classic":
            filter_chain += styled_progress_filter(design, seconds, fps, last, orbit=False)
            last = "with_progress"
        else:
            px = max(0, min(design.progress_x, 1080 - design.progress_width))
            py = max(0, min(design.progress_y, 1920 - design.progress_height))
            w, h = design.progress_width, design.progress_height
            filter_chain += (
                f"[{last}]drawbox=x={px}:y={py}:w={w}:h={h}:"
                "color=white@0.34:t=fill[progress_track];"
                f"color=c=white:s={w}x{h}:r={fps}:d={seconds:.3f},format=rgba,"
                "geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
                f"a='if(lte(X\\,W*min(T/{seconds:.3f}\\,1))\\,255\\,0)'[progress_fill];"
                f"[progress_track][progress_fill]overlay={px}:{py}:"
                "shortest=1[with_progress];"
            )
            last = "with_progress"

    fontfile = _timecode_fontfile() if design.timecode_enabled and not orbit else None
    # On Windows, never call drawtext without an explicit installed font:
    # MSYS2 builds can access-violate inside Fontconfig in that situation.
    if design.timecode_enabled and not orbit and fontfile != "":
        tx = min(design.timecode_x, 1060)
        ty = min(design.timecode_y, 1920 - design.timecode_font_size - 4)
        remaining = int(seconds + 0.5)
        total = f"{remaining // 60:02d}\\:{remaining % 60:02d}"
        # FFmpeg dynamic expressions: mm:ss / mm:ss, advancing each second.
        elapsed = (
            r"%{eif\:trunc(t/60)\:d\:2}\:"
            r"%{eif\:mod(t\,60)\:d\:2}"
        )
        filter_chain += (
            f"[{last}]drawtext="
            f"{('fontfile=' + fontfile + ':') if fontfile else ''}"
            f"text='{elapsed} / {total}':"
            f"fontcolor=white:fontsize={design.timecode_font_size}:"
            "borderw=2:bordercolor=black@0.70:"
            f"x={tx}:y={ty}:fix_bounds=1[with_timecode];"
        )
        last = "with_timecode"
    elif design.timecode_enabled and not orbit and fontfile == "":
        JOB.setdefault("notes", []).append(
            "Таймкод временно выключен: не найден доступный системный шрифт Windows. "
            "Видео и остальные элементы рендерятся."
        )

    filter_chain += f"[{last}]null[composite];"
    # Suppress the enormous FFmpeg version/configuration banner so the
    # dashboard can display actual filter and encoder errors on Windows.
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "error",
           "-y"]
    if performance["filter_threads"] is not None:
        cmd += ["-filter_complex_threads", str(performance["filter_threads"])]
    cmd += ["-i", str(source), *bg_input]
    if orbit_asset is not None:
        cmd += ["-loop", "1", "-framerate", str(fps), "-i", str(orbit_asset)]

    if design.banner_enabled:
        cmd += banner_input_args(banner)
        filter_chain += banner_overlay_filter(design, 3 if orbit else 2, fps)
    else:
        filter_chain += (
            f"[composite]fps={fps},"
            f"{'scale=2160:3840:flags=lanczos,' if design.export_resolution == 2160 else ''}"
            "format=yuv420p[v]"
        )

    # Decoding two cached PNGs per frame is far cheaper than recalculating
    # geq masks over 1M+ pixels for every frame of a large circle.
    for asset in (mask_asset, border_asset):
        cmd += ["-loop", "1", "-framerate", str(fps), "-i", str(asset)]

    cmd += [
        "-filter_complex", filter_chain,
        "-map", "[v]", "-map", "0:a?", "-t", str(seconds),
        # Privacy-minded export: don't carry optional input metadata or
        # chapters into the new MP4. This does not change content ownership.
        "-map_metadata", "-1", "-map_metadata:s", "-1",
        "-map_chapters", "-1",
        "-c:v", "libx264", *performance["encoder"],
        *(["-threads", str(performance["video_threads"])]
          if performance["video_threads"] is not None else []),
        "-profile:v", "high", "-level:v",
        ("6.0" if fps > 60 else "5.2") if design.export_resolution == 2160 else ("5.2" if fps > 60 else ("5.1" if fps == 60 else "4.2")),
        "-pix_fmt", "yuv420p",
        "-color_primaries", "bt709", "-color_trc", "bt709",
        "-colorspace", "bt709",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", str(target),
    ]
    try:
        # A short Telegram circle must not monopolize the computer for
        # 30 minutes. A timed-out high-quality attempt is retried once
        # with the same template, progress bar, circle and chromakey banner.
        run_ffmpeg_render(
            cmd, timeout=min(420, max(120, seconds * 8)),
            cwd=str(ROOT) if _is_windows() else None,
        )
    except subprocess.TimeoutExpired as exc:
        if "timeout" not in _fallbacks:
            JOB.setdefault("notes", []).append(
                f"{source.name}: рендер превысил лимит времени; "
                "повторяю в быстром режиме 1080p/30 FPS, сохраняя шаблон, "
                "обводку, прогресс-бар и баннер."
            )
            target.unlink(missing_ok=True)
            return render(
                source, bg, target,
                design.model_copy(update={
                    "quality_preset": "standard", "render_speed": "quiet",
                    "smooth_motion": False, "source_max_fps": False,
                    "output_fps": 30, "export_resolution": 1080,
                }), banner,
                _fallbacks=_fallbacks | {"timeout"},
            )
        raise RuntimeError(
            "FFmpeg не успел обработать видео даже в быстром режиме. "
            "Проверь длительность источника и производительность ПК."
        ) from exc
    except subprocess.CalledProcessError as exc:
        # A CalledProcessError normally begins with the entire ffmpeg command.
        # The Telegram Studio UI previously truncated it before the real
        # stderr diagnostics, making every failure look identical.
        details = (exc.stderr or exc.stdout or "").strip()
        # Windows status 0xC0000005 is an access violation. With Fontconfig
        # failures in drawtext, rerender once WITHOUT timecode rather than
        # dropping a downloaded Telegram circle or marking the upload complete.
        access_violation = (exc.returncode & 0xffffffff) == 0xC0000005
        fontconfig_failure = ("Fontconfig error" in details or
                              "Cannot load default config file" in details)
        if (_is_windows() and design.timecode_enabled and
                (fontconfig_failure or access_violation)):
            JOB.setdefault("notes", []).append(
                f"{source.name}: FFmpeg не смог обработать таймкод "
                "(Fontconfig/сбой Windows); повторяю монтаж без таймкода. "
                "Кружок, обводка, прогресс-бар и баннер сохраняются."
            )
            target.unlink(missing_ok=True)
            return render(
                source, bg, target,
                design.model_copy(update={"timecode_enabled": False}), banner,
                _fallbacks=_fallbacks | {"timecode"},
            )
        # Optical flow may exhaust memory with 120fps/high-resolution sources.
        # Retry the identical template and FPS without synthesized frames;
        # do not discard the circle, ring, progress bar, or video banner.
        if "minterpolate=" in filter_chain and "motion" not in _fallbacks:
            JOB.setdefault("notes", []).append(
                f"{source.name}: оптическая интерполяция не удалась; "
                "повторяю с исходными кадрами, сохраняя шаблон, FPS и баннер."
            )
            target.unlink(missing_ok=True)
            return render(
                source, bg, target,
                design.model_copy(update={"smooth_motion": False}), banner,
                _fallbacks=_fallbacks | {"motion"},
            )
        # Some FFmpeg builds cannot initialize advanced alpha/despill filters.
        # Keep the user's template and retry with the basic RGB colorkey.
        if (design.banner_enabled and design.chromakey_enabled and
                design.chromakey_mode == "advanced" and
                "chroma" not in _fallbacks and
                ("No such filter" in details or
                 "Error initializing complex filters" in details or
                 "Failed to configure output pad" in details)):
            JOB.setdefault("notes", []).append(
                f"{source.name}: усиленный хромакей недоступен; "
                "повторяю с RGB-хромакеем, сохраняя шаблон и баннер."
            )
            target.unlink(missing_ok=True)
            return render(
                source, bg, target,
                design.model_copy(update={"chromakey_mode": "standard"}), banner,
                _fallbacks=_fallbacks | {"chroma"},
            )
        if (performance["quality"] != "standard" and
                (access_violation or "No such filter" in details or
                 "Error initializing complex filters" in details)):
            # Optional visual filters must never make the previously working
            # classic Telegram-circle renderer unusable.
            JOB.setdefault("notes", []).append(
                f"{source.name}: расширенная обработка недоступна в FFmpeg. "
                "Повторяю с прежним качеством, сохраняя композицию."
            )
            target.unlink(missing_ok=True)
            return render(
                source, bg, target,
                design.model_copy(update={"quality_preset": "standard",
                                          "smooth_motion": False}), banner,
                _fallbacks=_fallbacks | {"quality"},
            )
        meaningful = [
            line.strip() for line in details.splitlines()
            if line.strip() and not line.lstrip().startswith((
                "ffmpeg version ", "built with ", "configuration:",
                "libavutil ", "libavcodec ", "libavformat ",
                "libavdevice ", "libavfilter ", "libswscale ",
                "libswresample ", "libpostproc ",
            ))
        ]
        # Preserve the END of the diagnostic. That is where FFmpeg reports
        # 'No such filter', 'Invalid argument', and filter init failures.
        diagnostics = "\n".join(meaningful[-12:])[-1550:]
        raise RuntimeError(
            "FFmpeg не смог смонтировать кружок (код "
            + str(exc.returncode) + "): "
            + (diagnostics or "FFmpeg завершился без подробной ошибки")
        ) from exc


async def process(limit, order="oldest", date_from=None, date_to=None, *,
                  design: RenderDesign, banner: Path | None,
                  output_dir: Path | None = None, target_slot: int = 0,
                  media_mode: Literal["circles", "videos"] = "circles",
                  video_layout=None, video_banner: Path | None = None,
                  video_banner_design: RenderDesign | None = None,
                  force_rerender: bool = False):
    JOB.update(status="running", total=0, done=0, errors=[], notes=[],
               stopping=False, current_file=None, current_started_at=None,
               media_mode=media_mode)
    try:
        d = config()
        d.setdefault("processed", [])
        output_dir = output_dir or settings.upload_dir
        media_namespace = "" if media_mode == "circles" else "videos:"
        render_scope = media_namespace + (
            (str(target_slot) + "@" + str(output_dir.resolve())) if target_slot else
            ("folder:" + str(output_dir.resolve())
             if output_dir.resolve() != settings.upload_dir.resolve() else "")
        )
        if render_scope:
            completed_keys = d.setdefault("processed_by_target", {}).setdefault(render_scope, [])
            known_hashes = set(d.setdefault("rendered_hashes_by_target", {}).get(render_scope, []))
        else:
            completed_keys = d["processed"]
            known_hashes = rendered_hashes(d)
            d["rendered_hashes"] = sorted(known_hashes)
        save(d)
        bgs = choose_backgrounds(design)
        normal_layout = video_layout
        if media_mode == "videos" and normal_layout is None:
            from app.instagram_studio import current_layout
            normal_layout = current_layout()
        background_queue = []
        previous_bg = None
        def next_background():
            nonlocal previous_bg
            if not background_queue:
                background_queue.extend(random.sample(bgs, len(bgs)))
                if previous_bg is not None and len(background_queue) > 1 and background_queue[0] == previous_bg:
                    background_queue[0], background_queue[1] = background_queue[1], background_queue[0]
            previous_bg = background_queue.pop(0)
            return previous_bg
        async with connected_telegram_client() as client:
            if not await client.is_user_authorized():
                raise ValueError("Сначала авторизуй Telegram")
            # Gather candidates from every channel before selecting any output.
            pools = {}
            channels = list(d["channels"])
            random.shuffle(channels)
            for channel in channels:
                if STOP_REQUESTED:
                    break
                try:
                    entity = await client.get_entity(channel)
                    candidates = []
                    found_selected_type = False
                    async for message in client.iter_messages(entity, limit=3000):
                        if STOP_REQUESTED:
                            break
                        if date_from and message.date.date() < date_from:
                            break
                        if date_to and message.date.date() > date_to:
                            continue
                        if not telegram_media_matches(message, media_mode):
                            continue
                        found_selected_type = True
                        key = (f"{channel}:{message.id}" if media_mode == "circles"
                               else f"video:{channel}:{message.id}")
                        filename_prefix = "tg" if media_mode == "circles" else "tgvideo"
                        target = output_dir / f"{filename_prefix}_{channel}_{message.id}.mp4"
                        if should_skip_existing_render(
                            key, target, completed_keys,
                            force_rerender=force_rerender,
                        ):
                            if target.is_file() and target.stat().st_size > 0 and key not in completed_keys:
                                completed_keys.append(key)
                            continue
                        candidates.append(message)
                    if not found_selected_type and not STOP_REQUESTED:
                        missing_type = ("кружков (видеосообщений)" if media_mode == "circles"
                                        else "обычных видео")
                        JOB["notes"].append(
                            f"@{channel}: среди проверенных сообщений не найдено {missing_type}. "
                            "Неподходящий тип не скачивается."
                        )
                    if order == "oldest":
                        candidates.reverse()
                    elif order == "random":
                        random.shuffle(candidates)
                    pools[channel] = candidates[:limit]
                except Exception as exc:
                    JOB["errors"].append(f"{channel}: {str(exc)[:250]}")
            # Randomize channel order each round; cap remains per channel.
            selected = []
            while any(pools.values()):
                round_channels = [name for name, items in pools.items() if items]
                random.shuffle(round_channels)
                for name in round_channels:
                    selected.append((name, pools[name].pop(0)))
            for channel, message in selected:
                if STOP_REQUESTED:
                    break
                # Re-check at download time as well as during discovery.
                # Never download/render an ordinary clip in circle mode (or
                # vice versa), even if the selection pool is modified.
                if not telegram_media_matches(message, media_mode):
                    continue
                key = (f"{channel}:{message.id}" if media_mode == "circles"
                       else f"video:{channel}:{message.id}")
                JOB["total"] += 1
                target_prefix = "tg" if media_mode == "circles" else "tgvideo"
                originals_dir = ORIGINALS if media_mode == "circles" else REGULAR_ORIGINALS
                source = originals_dir / f"{channel}_{message.id}.mp4"
                target = output_dir / f"{target_prefix}_{channel}_{message.id}.mp4"
                target_preexisting = target.is_file() and target.stat().st_size > 0
                try:
                    # Existing MP4s are preserved by default. Forced rerender writes
                    # to a temporary file first and replaces the old output only
                    # after FFprobe validates the newly rendered video.
                    if target_preexisting and not force_rerender:
                        if key not in completed_keys:
                            completed_keys.append(key)
                        save(d)
                        continue
                    # Re-download incomplete or unreadable originals rather than retrying a bad cache.
                    if source.exists():
                        try:
                            if source.stat().st_size == 0 or duration(source) <= 0:
                                raise ValueError("Empty or invalid media")
                        except (OSError, ValueError, subprocess.CalledProcessError):
                            source.unlink(missing_ok=True)
                    if not source.exists():
                        temp_source = source.with_suffix(".part.mp4")
                        temp_source.unlink(missing_ok=True)
                        try:
                            await client.download_media(message, file=str(temp_source))
                            if not temp_source.exists() or duration(temp_source) <= 0:
                                raise ValueError("Telegram прислал повреждённое или неполное видео")
                            temp_source.replace(source)
                        finally:
                            temp_source.unlink(missing_ok=True)
                    if not source.exists():
                        raise ValueError("Не удалось скачать Telegram-видео")
                    digest = file_digest(source)
                    if should_skip_known_source(
                        digest, known_hashes, force_rerender=force_rerender,
                    ):
                        # Same original media may have been forwarded to another channel.
                        if key not in completed_keys:
                            completed_keys.append(key)
                        save(d)
                        continue
                    # Render to a temporary file, publish only after ffprobe validates it.
                    JOB["current_file"] = source.name
                    JOB["current_started_at"] = time.time()
                    temp_target = target.with_name(target.stem + ".rendering.mp4")
                    try:
                        bg = next_background()
                        if media_mode == "circles":
                            await asyncio.to_thread(render, source, bg, temp_target, design, banner)
                        else:
                            from app.instagram_studio import render as render_normal_video
                            normal_preset = normal_layout.model_copy(update={
                                "background_mode": "single", "background_name": bg.name,
                            })
                            await asyncio.to_thread(
                                render_normal_video, source, temp_target, bg, normal_preset,
                                banner=video_banner, banner_design=video_banner_design,
                            )
                        if duration(temp_target) <= 0:
                            raise ValueError("Готовый ролик не прошёл проверку FFprobe")
                        temp_target.replace(target)
                    finally:
                        temp_target.unlink(missing_ok=True)
                    known_hashes.add(digest)
                    if render_scope:
                        d.setdefault("rendered_hashes_by_target", {})[render_scope] = sorted(known_hashes)
                    else:
                        d["rendered_hashes"] = sorted(known_hashes)
                    # Keep the processed-key index unique after a forced rebuild.
                    completed_keys[:] = [item for item in completed_keys if item != key]
                    completed_keys.append(key)
                    d.setdefault("last_outputs", {})[media_mode] = {
                        "name": target.name,
                        "path": str(target.resolve()),
                        "updated_at": int(time.time()),
                        "banner_applied": bool(
                            design.banner_enabled if media_mode == "circles"
                            else (video_banner_design and video_banner_design.banner_enabled)
                        ),
                    }
                    save(d)
                    JOB["done"] += 1
                except RenderStopped:
                    JOB.setdefault("notes", []).append(
                        "Рендер остановлен пользователем. Незавершённый MP4 удалён."
                    )
                    break
                except Exception as exc:
                    # Keep the previous valid MP4 if a forced rebuild fails.
                    if not target_preexisting:
                        target.unlink(missing_ok=True)
                    # The error is shown in Telegram Studio, not just the
                    # shortened FFmpeg argument list.
                    JOB["errors"].append(f"{key}: {str(exc)[-1750:]}")
                finally:
                    JOB["current_file"] = None
                    JOB["current_started_at"] = None

        JOB["status"] = "stopped" if STOP_REQUESTED else "done"
    except asyncio.CancelledError:
        JOB["status"] = "stopped"
        raise
    except Exception as exc:
        JOB["status"] = "error"
        JOB["errors"].append(str(exc)[:300])
    finally:
        JOB["current_file"] = None
        JOB["current_started_at"] = None
        JOB["stopping"] = False
        LOCK.release()


@router.post("/run")
async def run(body: RenderRequest):
    if LOCK.locked():
        raise HTTPException(409, "Обработка уже запущена")
    if not 1 <= body.limit_per_channel <= 500:
        raise HTTPException(400, "Лимит 1–500")
    if body.order not in ("oldest", "newest", "random"):
        raise HTTPException(400, "Некорректный порядок")
    if body.date_from and body.date_to and body.date_from > body.date_to:
        raise HTTPException(400, "Начальная дата позже конечной")
    # Snapshot settings at batch start so every Reel uses one consistent layout.
    data = config()
    design = effective_design(data)
    try:
        if body.target_slot:
            chosen_folder = (account_folder(body.target_slot) if body.output_folder == "linked"
                             else body.output_folder)
            selected_bg = account_background(body.target_slot, "telegram")
            if selected_bg:
                design = design.model_copy(update={
                    "background_mode": "single", "background_name": selected_bg,
                })
        else:
            if body.output_folder == "linked":
                raise ValueError("Выбери Instagram-аккаунт для привязанной папки")
            chosen_folder = body.output_folder
        output_dir = folder_path(chosen_folder, writable=True)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    banner = current_banner(data)
    video_layout = None
    video_banner = None
    video_design = None
    if body.media_mode == "videos":
        from app.instagram_studio import (
            current_layout, video_banner_file, video_banner_design as video_design_from_layout,
        )
        video_layout = current_layout()
        video_banner = video_banner_file()
        video_design = video_design_from_layout(video_layout)
        if video_design.banner_enabled and video_banner is None:
            raise HTTPException(400, "Загрузи баннер во вкладке «Instagram · видео»")
    elif design.banner_enabled and banner is None:
        raise HTTPException(400, "Загрузи баннер во вкладке «Telegram · кружки»")
    try:
        choose_backgrounds(design)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    global JOB_TASK, STOP_REQUESTED
    await LOCK.acquire()
    STOP_REQUESTED = False
    JOB_TASK = asyncio.create_task(process(
        body.limit_per_channel, body.order, body.date_from, body.date_to,
        design=design, banner=banner, output_dir=output_dir, target_slot=body.target_slot,
        media_mode=body.media_mode, video_layout=video_layout,
        video_banner=video_banner, video_banner_design=video_design,
        force_rerender=body.force_rerender,
    ))
    return {"started": True, "media_mode": body.media_mode}


async def shutdown_telegram_studio():
    global STOP_REQUESTED
    STOP_REQUESTED = True
    with ACTIVE_RENDER_LOCK:
        active = ACTIVE_RENDER_PROCESS
        if active is not None and active.poll() is None:
            active.terminate()
    if JOB_TASK is not None and not JOB_TASK.done():
        await JOB_TASK
    async with QR_LOCK:
        await qr_cleanup()
