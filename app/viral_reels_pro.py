"""Viral Reels Pro — isolated template #3. Templates #1/#2 are not touched.

Own projects, inputs, render jobs, fingerprints and statistics; FFmpeg is required.
Optional faster-whisper can provide real word timestamps, never fake captions.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, HTTPException, File, UploadFile, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from PIL import Image, ImageOps
from app.config import settings
from app.telegram_studio import BACKGROUNDS, current_banner

router = APIRouter(prefix="/api/viral-pro", tags=["viral-reels-pro"])
ROOT = settings.data_dir / "viral_reels_pro"
PROJECTS = ROOT / "projects"
MEDIA = ROOT / "media"
OWN_BACKGROUNDS = ROOT / "backgrounds"
OWN_BANNERS = ROOT / "banners"
EXPORTS = ROOT / "exports"
TMP = ROOT / "temporary"
PREVIEWS = ROOT / "previews"
for d in (ROOT, PROJECTS, MEDIA, OWN_BACKGROUNDS, OWN_BANNERS, EXPORTS, TMP, PREVIEWS):
    d.mkdir(parents=True, exist_ok=True)
JOBS: dict[str, dict] = {}
JOB_TASKS: dict[str, asyncio.Task] = {}
RENDER_LOCK = asyncio.Lock()
ID_RE = re.compile(r"^[0-9a-f]{32}$")
MEDIA_EXT = {".mp4", ".mov", ".m4v", ".webm"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}


def _local(request: Request):
    if not request.client or request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "Viral Pro доступен только с локального компьютера")
    # Prevent unrelated web pages from invoking local POST/PUT endpoints.
    origin = request.headers.get("origin")
    if origin and origin not in (
        "http://127.0.0.1:8765", "http://localhost:8765",
        f"http://127.0.0.1:{settings.port}", f"http://localhost:{settings.port}",
    ):
        raise HTTPException(403, "Чужой сайт не может изменить проект Viral Pro")


def _id(value: str) -> str:
    if not ID_RE.fullmatch(value):
        raise HTTPException(400, "Неверный ID")
    return value


def _json_write(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _project(project_id: str) -> dict:
    p = PROJECTS / (_id(project_id) + ".json")
    if not p.is_file():
        raise HTTPException(404, "Проект не найден")
    return json.loads(p.read_text(encoding="utf-8"))


def _save(p: dict):
    p["updated_at"] = datetime.now(timezone.utc).isoformat()
    _json_write(PROJECTS / (p["id"] + ".json"), p)


def _source(ident: str) -> Path:
    item = MEDIA / (_id(ident) + ".mp4")
    if not item.is_file():
        raise HTTPException(404, "Медиафайл не найден")
    return item


def _probe(path: Path) -> dict:
    try:
        p = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format",
             "-of", "json", str(path)],
            capture_output=True, text=True, check=True, timeout=30,
        )
        raw = json.loads(p.stdout)
        video = next(x for x in raw["streams"] if x["codec_type"] == "video")
        fps_fraction = video.get("avg_frame_rate") or "0/1"
        num, den = (float(x) for x in fps_fraction.split("/"))
        return {
            "duration": float(raw["format"].get("duration", 0)),
            "width": int(video["width"]), "height": int(video["height"]),
            "fps": round(num / den, 3) if den else 0,
            "audio": any(x["codec_type"] == "audio" for x in raw["streams"]),
            "codec": video.get("codec_name", ""),
        }
    except Exception as exc:
        raise ValueError(f"Не удалось прочитать MP4 через ffprobe: {str(exc)[-300:]}") from None


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _frame_hash(path: Path, second: float) -> str:
    """Compact 64-bit difference hash, comparable with Hamming distance."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg не установлен")
    with tempfile.TemporaryDirectory(dir=TMP) as directory:
        frame = Path(directory) / "frame.png"
        p = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-ss", str(max(0, second)), "-i", str(path), "-frames:v", "1",
             str(frame)], capture_output=True, timeout=40,
        )
        if p.returncode or not frame.exists():
            return ""
        with Image.open(frame) as img:
            gray = ImageOps.grayscale(img).resize((9, 8))
            values = list(gray.getdata())
            bits = "".join("1" if values[y * 9 + x] > values[y * 9 + x + 1]
                           else "0" for y in range(8) for x in range(8))
            return f"{int(bits, 2):016x}"


def _audio_hash(path: Path) -> str:
    """Exact-ish normalized PCM chunk fingerprint; not a music ID service."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-t", "8",
             "-ac", "1", "-ar", "8000", "-f", "s16le", "pipe:1"],
            capture_output=True, timeout=30,
        )
        return hashlib.sha256(result.stdout).hexdigest() if result.stdout else ""
    except Exception:
        return ""


def fingerprint(path: Path) -> dict:
    info = _probe(path)
    length = info["duration"]
    times = [min(max(0, length - .1), length * t) for t in (.1, .5, .9)]
    return {
        "sha256": _sha(path),
        "frames": [_frame_hash(path, t) for t in times],
        "audio_hash": _audio_hash(path),
        "duration": round(length, 3), "fps": info["fps"],
    }


def _similarity(a: dict, b: dict) -> dict:
    exact = bool(a.get("sha256") and a.get("sha256") == b.get("sha256"))
    xs, ys = a.get("frames", []), b.get("frames", [])
    scores = [
        1 - ((int(x, 16) ^ int(y, 16)).bit_count() / 64)
        for x, y in zip(xs, ys) if x and y
    ]
    visual = round(sum(scores) / len(scores), 3) if scores else None
    audio = bool(a.get("audio_hash") and a.get("audio_hash") == b.get("audio_hash"))
    return {"exact": exact, "visual": visual, "audio_exact": audio}


class Clip(BaseModel):
    media_id: str
    start: float = Field(default=0, ge=0)
    end: float | None = Field(default=None, ge=0)


class Word(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str = Field(max_length=120)


class Config(BaseModel):
    clips: list[Clip] = Field(default_factory=list, max_length=20)
    auto_trim_silences: bool = True
    auto_captions: bool = True
    background_name: str = ""
    background_source: Literal["studio", "local"] = "studio"
    banner_enabled: bool = False
    banner_file: str = ""
    banner_x: int = Field(default=220, ge=-1080, le=1080)
    banner_y: int = Field(default=1500, ge=-1920, le=1920)
    banner_width: int = Field(default=600, ge=80, le=1080)
    banner_chroma_enabled: bool = False
    banner_chroma_color: str = Field(default="#00FF00", pattern=r"^#[0-9a-fA-F]{6}$")
    banner_chroma_similarity: float = Field(default=0.22, ge=0.01, le=0.6)
    banner_chroma_blend: float = Field(default=0.07, ge=0, le=0.35)
    banner_start: float = Field(default=0, ge=0)
    banner_end: float = Field(default=3600, gt=0)
    circle_x: int = Field(default=90, ge=0, le=900)
    circle_y: int = Field(default=220, ge=0, le=1700)
    circle_size: int = Field(default=900, ge=240, le=1080)
    smooth: Literal["off", "standard", "pro", "auto"] = "auto"
    fps: Literal[30, 60] = 30
    bitrate_mbps: int = Field(default=9, ge=4, le=16)
    contrast: float = Field(default=1.05, ge=.5, le=1.5)
    saturation: float = Field(default=1.08, ge=0, le=2)
    denoise: bool = True
    loudness: bool = True
    captions: list[Word] = Field(default_factory=list, max_length=2000)
    caption_font: Literal["Arial", "Impact", "Verdana", "Georgia", "Trebuchet MS", "DejaVu Sans"] = "Arial"
    caption_template: Literal["classic", "bold", "highlight", "minimal", "box"] = "classic"
    caption_font_size: int = Field(default=48, ge=20, le=90)
    caption_y: int = Field(default=820, ge=100, le=1800)
    caption_color: str = "#FFFFFF"
    mute_background: bool = True


class Stats(BaseModel):
    variant: Literal["A", "B", "C"]
    views: int | None = Field(default=None, ge=0)
    follower_reach: int | None = Field(default=None, ge=0)
    nonfollower_reach: int | None = Field(default=None, ge=0)
    average_watch_seconds: float | None = Field(default=None, ge=0)
    completion_rate: float | None = Field(default=None, ge=0, le=100)
    likes: int | None = Field(default=None, ge=0)
    sends: int | None = Field(default=None, ge=0)
    saves: int | None = Field(default=None, ge=0)


def _background(cfg: Config) -> Path | None:
    if not cfg.background_name:
        return None
    if Path(cfg.background_name).name != cfg.background_name:
        raise ValueError("Некорректный файл фона")
    root = OWN_BACKGROUNDS if cfg.background_source == "local" else BACKGROUNDS
    path = root / cfg.background_name
    return path if path.is_file() else None


def _banner(cfg: Config) -> Path | None:
    if not cfg.banner_enabled:
        return None
    if cfg.banner_file:
        if Path(cfg.banner_file).name != cfg.banner_file:
            raise ValueError("Некорректный файл баннера")
        path = OWN_BANNERS / cfg.banner_file
        return path if path.is_file() else None
    return current_banner()


async def _upload_asset(file: UploadFile, directory: Path, max_bytes: int) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in MEDIA_EXT | IMAGE_EXT:
        raise HTTPException(400, "Файл должен быть MP4, WebM, PNG, JPG или WebP")
    name = uuid.uuid4().hex + suffix
    dst = directory / name
    total = 0
    try:
        with dst.open("wb") as stream:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(413, "Файл слишком большой")
                stream.write(chunk)
        if suffix in IMAGE_EXT:
            with Image.open(dst) as im:
                im.verify()
            with Image.open(dst) as im:
                if im.width < 32 or im.height < 32 or im.width > 8192 or im.height > 8192:
                    raise HTTPException(400, "Неверные размеры изображения")
        else:
            await asyncio.to_thread(_probe, dst)
        return {"name": name, "bytes": total}
    except Exception:
        dst.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@router.post("/background")
async def upload_background(request: Request, file: UploadFile = File(...)):
    _local(request)
    return await _upload_asset(file, OWN_BACKGROUNDS, 300 * 1024 * 1024)


@router.post("/banner")
async def upload_banner(request: Request, file: UploadFile = File(...)):
    _local(request)
    return await _upload_asset(file, OWN_BANNERS, 100 * 1024 * 1024)


@router.get("/preview-asset/{kind}/{name}")
def preview_asset(kind: Literal["background", "studio-background", "banner"], name: str,
                  request: Request):
    """Read-only approved media preview; never expose an arbitrary local file."""
    _local(request)
    if not name or Path(name).name != name or "/" in name or "\\" in name:
        raise HTTPException(400, "Недопустимое имя файла")
    if Path(name).suffix.lower() not in MEDIA_EXT | IMAGE_EXT:
        raise HTTPException(400, "Недопустимое расширение")
    directory = {
        "background": OWN_BACKGROUNDS,
        "studio-background": BACKGROUNDS,
        "banner": OWN_BANNERS,
    }[kind]
    target = directory / name
    if not target.is_file():
        raise HTTPException(404, "Файл не найден")
    return FileResponse(target, headers={"Cache-Control": "no-store"})


@router.get("/preview-studio-banner")
def preview_studio_banner(request: Request):
    _local(request)
    target = current_banner()
    if not target or not target.is_file():
        raise HTTPException(404, "Баннер Telegram Studio не найден")
    return FileResponse(target, headers={"Cache-Control": "no-store"})


@router.get("/capabilities")
def capabilities(request: Request):
    _local(request)
    try:
        import faster_whisper  # noqa: F401
        whisper = True
    except ImportError:
        whisper = False
    filters = ""
    if shutil.which("ffmpeg"):
        try:
            filters = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                capture_output=True, text=True, timeout=15).stdout
        except Exception:
            pass
    return {
        "ffmpeg": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
        "optical_flow": "minterpolate" in filters,
        "captions_ffmpeg": " ass " in filters or " subtitles " in filters,
        "whisper": whisper, "rife": False,
        "backgrounds": [
            {"name": f.name, "image": f.suffix.lower() in IMAGE_EXT}
            for f in sorted(BACKGROUNDS.iterdir())
            if f.is_file() and f.suffix.lower() in MEDIA_EXT | IMAGE_EXT
        ],
        "banner_available": bool(current_banner()),
        "local_backgrounds": [
            {"name": f.name, "image": f.suffix.lower() in IMAGE_EXT}
            for f in sorted(OWN_BACKGROUNDS.iterdir())
            if f.is_file() and f.suffix.lower() in MEDIA_EXT | IMAGE_EXT
        ],
        "notes": ["RIFE и GPU-интерполяция не включены: PRO использует FFmpeg minterpolate.",
                  "AI-транскрипция требует дополнительно установленного faster-whisper."]
    }


@router.post("/media")
async def upload_media(request: Request, file: UploadFile = File(...)):
    _local(request)
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in MEDIA_EXT:
        raise HTTPException(400, "Загрузи MP4, MOV, M4V или WebM")
    if not shutil.which("ffprobe"):
        raise HTTPException(503, "FFprobe не установлен")
    ident = uuid.uuid4().hex
    temp = TMP / (ident + ".upload")
    target = MEDIA / (ident + ".mp4")
    limit = 300 * 1024 * 1024
    size = 0
    try:
        with temp.open("wb") as out:
            while block := await file.read(1024 * 1024):
                size += len(block)
                if size > limit:
                    raise HTTPException(413, "Максимальный размер исходника 300 МБ")
                out.write(block)
        info = await asyncio.to_thread(_probe, temp)
        if info["duration"] <= .1 or info["duration"] > 900:
            raise HTTPException(400, "Видео должно быть от 0.1 до 900 секунд")
        os.replace(temp, target)
        return {"id": ident, "name": Path(file.filename or "circle").name,
                "bytes": size, "probe": info}
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@router.get("/media/{media_id}")
def serve_media(media_id: str, request: Request):
    _local(request)
    return FileResponse(_source(media_id), media_type="video/mp4")


@router.post("/projects")
def create_project(request: Request):
    _local(request)
    ident = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    project = {"id": ident, "template": "viral_reels_pro_v3",
               "created_at": now, "updated_at": now, "config": Config().model_dump(),
               "exports": [], "metrics": [], "analysis": {},
               "recommendations": []}
    _save(project)
    return project


@router.get("/projects")
def list_projects(request: Request):
    _local(request)
    result = []
    for f in sorted(PROJECTS.glob("*.json"), reverse=True):
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
            result.append({"id": p["id"], "updated_at": p["updated_at"],
                           "exports": len(p.get("exports", []))})
        except (KeyError, json.JSONDecodeError):
            continue
    return result


@router.get("/projects/{project_id}")
def get_project(project_id: str, request: Request):
    _local(request)
    return _project(project_id)


@router.put("/projects/{project_id}")
def update_project(project_id: str, body: Config, request: Request):
    _local(request)
    p = _project(project_id)
    if JOBS.get(project_id, {}).get("status") == "running":
        raise HTTPException(409, "Нельзя редактировать во время рендера")
    for clip in body.clips:
        video = _source(clip.media_id)
        d = _probe(video)["duration"]
        if clip.start >= d or (clip.end is not None and (clip.end > d + .02 or clip.end <= clip.start)):
            raise HTTPException(400, "Границы фрагмента вне длительности файла")
    if body.background_name and not _background(body):
        raise HTTPException(400, "Выбранный фон отсутствует")
    if body.banner_enabled and not _banner(body):
        raise HTTPException(400, "Выбранный рекламный баннер отсутствует")
    if body.banner_enabled and body.banner_start >= body.banner_end:
        raise HTTPException(400, "Укажи конец показа баннера позже начала")
    if body.circle_x + body.circle_size > 1080 or body.circle_y + body.circle_size > 1920:
        raise HTTPException(400, "Кружок выходит за пределы кадра 1080×1920")
    p["config"] = body.model_dump()
    _save(p)
    return p


def _black_frames(path: Path) -> list[dict]:
    try:
        result = subprocess.run(
            ["ffmpeg", "-v", "info", "-t", "3", "-i", str(path),
             "-vf", "blackdetect=d=0.2:pix_th=0.08", "-an",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=40,
        )
        return [
            {"start": float(start), "end": float(end)}
            for start, end in re.findall(
                r"black_start:([\d.]+)\s+black_end:([\d.]+)", result.stderr
            )
        ][:15]
    except Exception:
        return []


def _first_three_seconds(path: Path, pauses: list[dict], info: dict) -> dict:
    length = min(3.0, info["duration"])
    stamps = [0.15, min(.85, max(.1, length - .1)),
              min(2.2, max(.1, length - .1))]
    hashes = [_frame_hash(path, t) for t in stamps]
    movement = [
        (int(a, 16) ^ int(b, 16)).bit_count()
        for a, b in zip(hashes, hashes[1:]) if a and b
    ]
    start_silence = any(
        s["start"] <= .15 and s["end"] >= .5 for s in pauses
    )
    return {
        "duration_analyzed": length,
        "possible_static_start": bool(movement and max(movement) <= 3),
        "starting_silence": start_silence,
        "black_intervals": _black_frames(path),
        "frame_change_scores": movement,
        "note": "Оценка первых секунд по кадрам и звуку; не распознаёт лица или смысл",
    }


def _silences(path: Path, *, threshold: str = "-34dB") -> list[dict]:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
             "-af", f"silencedetect=noise={threshold}:d=0.45",
             "-f", "null", "-"], capture_output=True, text=True, timeout=180,
        )
        starts = re.findall(r"silence_start:\s*([0-9.]+)", result.stderr)
        ends = re.findall(r"silence_end:\s*([0-9.]+)", result.stderr)
        return [{"start": float(a), "end": float(b)}
                for a, b in zip(starts, ends) if float(b) > float(a)]
    except Exception:
        return []


@router.post("/projects/{project_id}/analyze")
async def analyze(project_id: str, request: Request):
    _local(request)
    p = _project(project_id)
    config = Config.model_validate(p["config"])
    if not config.clips:
        raise HTTPException(400, "Сначала загрузи кружок")
    return await _analyze_project(p)


async def _analyze_project(p: dict):
    config = Config.model_validate(p["config"])
    findings = []
    fingerprints = []
    for i, clip in enumerate(config.clips):
        path = _source(clip.media_id)
        fp, silences = await asyncio.gather(
            asyncio.to_thread(fingerprint, path),
            asyncio.to_thread(_silences, path),
        )
        fingerprints.append({"media_id": clip.media_id, **fp})
        intro = await asyncio.to_thread(
            _first_three_seconds, path, silences, _probe(path),
        )
        if intro["starting_silence"]:
            findings.append({"clip": i, "kind": "intro_silence",
                             "message": "В начале кружка есть заметная тишина."})
        if intro["black_intervals"]:
            findings.append({"clip": i, "kind": "black_frame",
                             "message": "В первые 3 секунды есть тёмные кадры.",
                             "intervals": intro["black_intervals"]})
        if intro["possible_static_start"]:
            findings.append({"clip": i, "kind": "static_intro",
                             "message": "Первые кадры мало меняются: проверь динамику начала."})
        fingerprints[-1]["intro_quality"] = intro
        if silences:
            findings.append({"clip": i, "kind": "silence",
                             "message": f"Найдено {len(silences)} пауз речи.",
                             "intervals": silences[:30]})
        if fp["fps"] < 24:
            findings.append({"clip": i, "kind": "motion",
                             "message": "Низкий FPS: AUTO рассмотрит интерполяцию."})
    if config.banner_enabled and (
        config.banner_x < config.circle_x + config.circle_size and
        config.banner_x + config.banner_width > config.circle_x and
        config.banner_y < config.circle_y + config.circle_size and
        config.banner_y + 200 > config.circle_y
    ):
        findings.append({
            "kind": "possible_banner_overlap",
            "message": "Баннер потенциально перекрывает кружок: проверь в готовом MP4."
        })
    p["analysis"] = {"sources": fingerprints,
                     "findings": findings,
                     "created_at": datetime.now(timezone.utc).isoformat()}
    p["recommendations"] = [
        "A Smooth: исходные фрагменты, спокойный темп и плавное движение.",
        "B Dynamic: укороченные стартовые/концевые паузы; проверь смысл реплик.",
        "C Retention: плотное начало, короткий финал; проверь бесшовность."
    ]
    _save(p)
    return {"analysis": p["analysis"], "recommendations": p["recommendations"]}


def _ass_timestamp(t: float) -> str:
    cs = max(0, round(t * 100))
    return f"{cs // 360000}:{(cs // 6000) % 60:02d}:{(cs // 100) % 60:02d}.{cs % 100:02d}"


def _caption_style(config: Config) -> tuple[str, str, int, int, int]:
    """Font, main colour, bold flag, outline and opaque-box mode."""
    palette = {
        "classic": (config.caption_color, -1, 4, 1),
        "bold": ("#FFFFFF", -1, 6, 1),
        "highlight": ("#FFE237", -1, 5, 1),
        "minimal": (config.caption_color, 0, 2, 1),
        "box": ("#FFFFFF", -1, 1, 3),
    }
    color, bold, outline, border_style = palette[config.caption_template]
    return config.caption_font, color, bold, outline, border_style


def _make_ass(words: list[Word], temp: Path, config: Config, offset: float = 0):
    font, color, bold, outline, border_style = _caption_style(config)
    color = color.lstrip("#")
    bgr = color[4:6] + color[2:4] + color[0:2]
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n"
            "[V4+ Styles]\n"
            "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,"
            "BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,"
            "BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
            f"Style: Viral,{font},{config.caption_font_size},&H00{bgr},&H0000D9FF,"
            f"&H00000000,&H90000000,{bold},0,0,0,100,100,0,0,"
            f"{border_style},{outline},1,2,50,50,80,1\n"
            "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n")
    lines = []
    for w in words:
        start, end = w.start - offset, w.end - offset
        if end <= 0 or start >= 900:
            continue
        safe = w.text.replace("{", "").replace("}", "").replace("\\", "")
        safe = safe.replace("\n", " ").replace(",", "，")
        lines.append(
            f"Dialogue: 0,{_ass_timestamp(max(0,start))},"
            f"{_ass_timestamp(max(start + .05,end))},Viral,,0,0,0,,"
            f"{{\\an5\\pos(540,{config.caption_y})}}{safe}\n"
        )
    temp.write_text(head + "".join(lines), encoding="utf-8-sig")

@router.post("/projects/{project_id}/transcribe")
async def transcribe(project_id: str, request: Request):
    _local(request)
    p = _project(project_id)
    config = Config.model_validate(p["config"])
    if not config.clips:
        raise HTTPException(400, "Нет кружков для распознавания")
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise HTTPException(503,
            "Установи faster-whisper для реальной транскрипции: "
            r".\.venv\Scripts\python.exe -m pip install faster-whisper") from None
    result = await _transcribe_words(config)
    p["config"]["captions"] = result
    _save(p)
    return {"words": result, "note": "AI-черновик: проверь текст и таймкоды вручную"}


async def _transcribe_words(config: Config) -> list[dict]:
    def recognize():
        model = WhisperModel("base", device="cpu", compute_type="int8")
        words = []
        offset = 0.0
        for clip in config.clips:
            path = _source(clip.media_id)
            segments, _ = model.transcribe(str(path), word_timestamps=True, vad_filter=True)
            length = (clip.end if clip.end is not None else _probe(path)["duration"]) - clip.start
            for segment in segments:
                for word in segment.words or []:
                    if word.end <= clip.start or word.start >= clip.start + length:
                        continue
                    words.append(Word(
                        start=offset + max(0, word.start - clip.start),
                        end=offset + min(length, word.end - clip.start),
                        text=word.word.strip()))
            offset += length
        return [w.model_dump() for w in words]
    result = await asyncio.to_thread(recognize)
    return result


def _effective_motion(config: Config, info: dict) -> str:
    if config.smooth == "off":
        return "off"
    if config.smooth == "standard":
        return "standard"
    if config.smooth == "pro":
        return "pro"
    return "pro" if info["fps"] and info["fps"] < 24 else "standard"


def _segment_bounds(clip: Clip, info: dict, variant: str, silence: list[dict]) -> tuple[float,float]:
    start = clip.start
    end = min(clip.end or info["duration"], info["duration"])
    if variant in ("B", "C"):
        # Conservative trims; never remove middle words automatically.
        for pause in silence:
            if (pause["start"] <= start + .08 and pause["end"] > start + .05
                    and pause["end"] <= start + 3):
                start = min(end - .35, max(start, pause["end"] - (.05 if variant == "C" else .18)))
                break
        for pause in reversed(silence):
            if end - pause["end"] <= .08 and pause["start"] >= end - 3:
                end = max(start + .35, min(end, pause["start"] + .1))
                break
    return start, end


def _ffmpeg_path(path: Path) -> str:
    # libass path option must escape colon/backslash for Windows FFmpeg.
    return str(path.resolve()).replace("\\", "/").replace(":", "\\\\:").replace("'", "\\'")


def _read_ffmpeg_seconds(path: Path) -> float | None:
    """Read actual media time from FFmpeg -progress, not elapsed wall time."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in reversed(lines):
            if line.startswith("out_time="):
                hh, mm, ss = line.partition("=")[2].strip().split(":")
                value = int(hh) * 3600 + int(mm) * 60 + float(ss)
                return value if math.isfinite(value) and value >= 0 else None
    except (OSError, ValueError, TypeError):
        pass
    return None


async def _ffmpeg(job: dict, cmd: list[str], output: Path, estimated: float,
                  index: int, total: int, cwd: Path | None = None):
    """Render with real output-time progress; preserve FFmpeg errors for debugging."""
    if not cmd or cmd[0] != "ffmpeg":
        raise ValueError("Expected FFmpeg executable")
    logfile = TMP / (job["id"] + "_ffmpeg.log")
    progress_file = TMP / (job["id"] + f"_{index:02d}_progress.txt")
    command = [cmd[0], "-progress", str(progress_file), "-stats_period", "0.5", *cmd[1:]]
    process = None
    succeeded = False
    try:
        with logfile.open("wb") as logs:
            process = subprocess.Popen(command, stderr=logs, stdout=subprocess.DEVNULL,
                                       cwd=str(cwd) if cwd else None)
            while process.poll() is None:
                if job.get("cancel"):
                    process.terminate()
                    try:
                        await asyncio.to_thread(process.wait, timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        await asyncio.to_thread(process.wait)
                    output.unlink(missing_ok=True)
                    raise asyncio.CancelledError()
                media_seconds = _read_ffmpeg_seconds(progress_file)
                if media_seconds is not None:
                    fraction = min(.99, media_seconds / max(.1, estimated))
                    job["progress"] = round((index + fraction) / total * 100, 1)
                await asyncio.sleep(.5)
        if job.get("cancel"):
            output.unlink(missing_ok=True)
            raise asyncio.CancelledError()
        if process.returncode:
            error = logfile.read_text(encoding="utf-8", errors="replace")[-1800:]
            output.unlink(missing_ok=True)
            job["error_log"] = logfile.name
            raise RuntimeError(f"FFmpeg вернул {process.returncode}: {error}")
        succeeded = True
        job["progress"] = round((index + 1) / total * 100, 1)
    except asyncio.CancelledError:
        if process and process.poll() is None:
            process.terminate()
            try:
                await asyncio.to_thread(process.wait, timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait)
        output.unlink(missing_ok=True)
        raise
    finally:
        progress_file.unlink(missing_ok=True)
        if succeeded:
            logfile.unlink(missing_ok=True)

async def _render_variant(project: dict, variant: str, job: dict, work: Path, position: int):
    config = Config.model_validate(project["config"])
    if not config.clips:
        raise ValueError("Нет исходных кружков")
    background = _background(config)
    if not background or not background.is_file():
        raise ValueError("Для Viral Reels Pro выбери фон из Telegram Studio")
    banner = _banner(config)
    if config.banner_enabled and not banner:
        raise ValueError("Рекламный баннер не найден")
    if config.smooth == "pro" and "minterpolate" not in _available_filters():
        raise ValueError("Optical Flow minterpolate недоступен в FFmpeg")
    clips = []
    offset = 0.0
    for i, clip in enumerate(config.clips):
        src = _source(clip.media_id)
        info = _probe(src)
        detected = _silences(src) if variant in ("B", "C") else []
        start, end = _segment_bounds(clip, info, variant, detected)
        length = end - start
        if length < .25:
            raise ValueError("Слишком короткий фрагмент для рендера")
        clips.append((src, info, start, length))
    steps_per_variant = len(clips) + 1
    steps_total = 3 * steps_per_variant
    segs = []
    original_offset = 0.0
    for index, (src, info, start, length) in enumerate(clips):
        if job.get("cancel"):
            raise asyncio.CancelledError()
        output = work / f"{variant}_segment_{index:02d}.mp4"
        motion = _effective_motion(config, info)
        if motion == "pro" and "minterpolate" not in _available_filters():
            motion = "standard"
            job["notes"].append(f"{src.name}: FFmpeg без optical flow; STANDARD")
        fps = config.fps
        interp = (f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=obmc,"
                  if motion == "pro" and info["fps"] < fps else f"fps={fps},")
        size = config.circle_size
        cx = size / 2
        crop = (
            f"[0:v]{interp}scale={size}:{size}:force_original_aspect_ratio=increase:"
            f"flags=lanczos,crop={size}:{size},format=rgba,"
            "geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
            f"a='if(lte((X-{cx})*(X-{cx})+(Y-{cx})*(Y-{cx}),{cx*cx}),255,0)'"
            "[circle];"
        )
        if background.suffix.lower() in IMAGE_EXT:
            bgfilter = (
                "[1:v]scale=1200:2134:flags=lanczos,"
                f"zoompan=z='min(zoom+0.00012,1.08)':"
                "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"d=1:s=1080x1920:fps={fps},setsar=1,"
                f"eq=contrast={config.contrast}:saturation={config.saturation}[bg];"
            )
        else:
            bgfilter = (
                "[1:v]scale=1080:1920:force_original_aspect_ratio=increase:"
                "flags=lanczos,crop=1080:1920,setsar=1,"
                f"eq=contrast={config.contrast}:saturation={config.saturation},"
                f"fps={fps}[bg];"
            )
        graph = crop + bgfilter + (
            f"[bg][circle]overlay={config.circle_x}:{config.circle_y}:"
            "shortest=1[comp];"
        )
        prev = "comp"
        cmd = ["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "error", "-y",
               "-ss", str(start), "-t", str(length), "-i", str(src)]
        if background.suffix.lower() in IMAGE_EXT:
            cmd += ["-loop", "1", "-i", str(background)]
        else:
            cmd += ["-stream_loop", "-1", "-i", str(background)]
        if banner:
            n = 2
            cmd += (["-loop", "1", "-i", str(banner)]
                    if banner.suffix.lower() in IMAGE_EXT else
                    ["-stream_loop", "-1", "-i", str(banner)])
            graph += (
                f"[{n}:v]scale={config.banner_width}:-1:flags=lanczos,"
                + (f"chromakey=0x{config.banner_chroma_color[1:]}:"
                   f"{config.banner_chroma_similarity:.3f}:"
                   f"{config.banner_chroma_blend:.3f},"
                   if config.banner_chroma_enabled else "")
                + "format=rgba,fade=t=in:st=0:d=0.3:alpha=1[ad];"
                f"[{prev}][ad]overlay={config.banner_x}:{config.banner_y}:"
                f"enable='between(t,{max(0,config.banner_start-offset)},{max(0,config.banner_end-offset)})'"
                "[withad];"
            )
            prev = "withad"
        graph += f"[{prev}]format=yuv420p[v]"
        # All inputs must appear before mapping output streams in FFmpeg.
        if not info["audio"]:
            silent_index = 3 if banner else 2
            cmd += ["-f", "lavfi", "-t", str(length), "-i", "anullsrc=r=48000:cl=stereo"]
        # Subtitles are applied as a separate filter after composition,
        # mapping the same per-project word timeline to the current clip.
        selected_clip = config.clips[index]
        original_length = (
            (selected_clip.end or info["duration"]) - selected_clip.start
        )
        # Manual/AI word timestamps belong to the ORIGINAL clip sequence.
        # When B/C remove silence, shift words by the actual trim, so captions
        # remain aligned with the surviving audio rather than drifting.
        caption_origin = original_offset + (start - selected_clip.start)
        words = [
            w for w in config.captions
            if caption_origin <= w.start < caption_origin + length
        ]
        if words:
            ass = work / f"sub_{variant}_{index}.ass"
            _make_ass(words, ass, config, caption_origin)
            # Relative ASS path + cwd avoids Windows drive-letter escaping.
            graph += f";[v]ass=filename={ass.name}[captioned]"
            videomap = "[captioned]"
        else:
            videomap = "[v]"
        cmd += ["-filter_complex", graph, "-map", videomap]
        if info["audio"]:
            cmd += ["-map", "0:a:0",
                    "-af", (
                        ",".join(
                            (["highpass=f=70", "lowpass=f=12500", "afftdn=nf=-25"]
                             if config.denoise else [])
                            + (["loudnorm=I=-16:TP=-1.5:LRA=11",
                                "alimiter=limit=0.95"] if config.loudness else [])
                        ) or "anull"
                    )]
        else:
            # All segments must have audio for concat (even silent clips).
            cmd += ["-map", f"{silent_index}:a"]
        cmd += [
            "-t", str(length), "-r", str(fps), "-c:v", "libx264",
            "-preset", "medium", "-b:v", f"{config.bitrate_mbps}M",
            "-maxrate", f"{config.bitrate_mbps + 2}M",
            "-bufsize", f"{(config.bitrate_mbps + 2) * 2}M",
            "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
            "-movflags", "+faststart", str(output)]
        job["stage"] = f"{variant}: кружок {index + 1}/{len(clips)}"
        await _ffmpeg(job, cmd, output, length,
                      position * steps_per_variant + index, steps_total, cwd=work)
        segs.append(output)
        offset += length
        original_offset += original_length
    if job.get("cancel"):
        raise asyncio.CancelledError()
    outdir = EXPORTS / project["id"]
    outdir.mkdir(exist_ok=True)
    export_id = uuid.uuid4().hex
    final = outdir / f"{export_id}_{variant}.mp4"
    job["stage"] = f"{variant}: переходы между сценами и итоговый MP4"
    lengths = [_probe(seg)["duration"] for seg in segs]
    transition = (min({"A": .28, "B": .13, "C": .08}[variant],
                      min(lengths) * .25)
                  if len(segs) > 1 and "xfade" in _available_filters() else 0)
    if transition and len(segs) > 1:
        cmd = ["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "error", "-y"]
        for seg in segs:
            cmd += ["-i", str(seg)]
        graph = []
        for i in range(len(segs)):
            graph += [f"[{i}:v]fps={config.fps},settb=AVTB[v{i}]",
                      f"[{i}:a]asetpts=PTS-STARTPTS[a{i}]"]
        video, audio = "v0", "a0"
        total_time = lengths[0]
        for i in range(1, len(segs)):
            output_v, output_a = f"vx{i}", f"ax{i}"
            graph.append(
                f"[{video}][v{i}]xfade=transition=fade:"
                f"duration={transition:.3f}:offset={total_time-transition:.3f}"
                f"[{output_v}]"
            )
            graph.append(
                f"[{audio}][a{i}]acrossfade=d={transition:.3f}:c1=tri:c2=tri"
                f"[{output_a}]"
            )
            video, audio = output_v, output_a
            total_time += lengths[i] - transition
        graph += [f"[{video}]format=yuv420p[v]", f"[{audio}]anull[a]"]
        cmd += [
            "-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "medium",
            "-b:v", f"{config.bitrate_mbps}M",
            "-maxrate", f"{config.bitrate_mbps + 2}M",
            "-bufsize", f"{(config.bitrate_mbps + 2)*2}M",
            "-pix_fmt", "yuv420p", "-r", str(config.fps),
            "-colorspace", "bt709", "-color_primaries", "bt709",
            "-color_trc", "bt709",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
            "-movflags", "+faststart", str(final),
        ]
    else:
        if len(segs) > 1:
            job["notes"].append("FFmpeg без xfade: соединяю сцены без плавного перехода.")
        concat = work / f"{variant}_list.txt"
        concat.write_text(
            "".join(f"file '{str(s).replace(chr(92), '/')}'\n" for s in segs),
            encoding="utf-8",
        )
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-f", "concat", "-safe", "0", "-i", str(concat),
               "-c", "copy", "-movflags", "+faststart", str(final)]
    await _ffmpeg(job, cmd, final, sum(lengths),
                  position * steps_per_variant + len(clips), steps_total, cwd=work)
    expected_duration = sum(lengths) - transition * (len(segs) - 1)
    info = _probe(final)
    if info["width"] != 1080 or info["height"] != 1920:
        final.unlink(missing_ok=True)
        raise RuntimeError("Контроль 9:16: экспорт имеет неверные размеры")
    duration_tolerance = min(.75, max(.18, expected_duration * .03))
    if abs(info["duration"] - expected_duration) > duration_tolerance:
        final.unlink(missing_ok=True)
        raise RuntimeError("Контроль экспорта: рассинхронизация общей длительности")
    if not info["audio"]:
        final.unlink(missing_ok=True)
        raise RuntimeError("Контроль экспорта: отсутствует звуковая дорожка")
    if abs(info["fps"] - config.fps) > 1:
        final.unlink(missing_ok=True)
        raise RuntimeError("Контроль экспорта: неверная частота кадров")
    fp = fingerprint(final)
    source_hashes = [
        {"media_id": ident, "sha256": _sha(_source(ident))}
        for ident in dict.fromkeys(c.media_id for c in config.clips)
    ]
    return {
        "id": export_id, "variant": variant, "template": "viral_reels_pro_v3",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sha256": fp["sha256"], "fingerprint": fp,
        "sources": [x.media_id for x in config.clips],
        "source_sha256": source_hashes,
        "provenance_note": "Локальный журнал монтажа и проверяемые хеши файлов; не является подтверждением авторства или гарантией решения платформы.",
        "source_fingerprints": [
            x for x in project.get("analysis", {}).get("sources", [])
            if x.get("media_id") in {c.media_id for c in config.clips}
        ],
        "file": final.name, "settings": config.model_dump(),
        "duration": info["duration"], "transition_seconds": round(transition, 3),
        "width": info["width"],
        "height": info["height"], "fps": info["fps"],
        "changes": (
            "A: оригинальный порядок, плавная обработка" if variant == "A" else
            "B: консервативное сокращение крайних пауз" if variant == "B" else
            "C: плотное начало и укороченный финал"
        ),
    }


def _available_filters() -> str:
    if not shutil.which("ffmpeg"):
        return ""
    try:
        return subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True, text=True, timeout=12
        ).stdout
    except Exception:
        return ""


async def _job_runner(project_id: str, job: dict):
    async with RENDER_LOCK:
        work = TMP / job["id"]
        work.mkdir()
        try:
            p = _project(project_id)
            job["status"] = "running"
            cfg = Config.model_validate(p["config"])
            job["stage"] = "1/4 · Безопасный автомонтаж крайних пауз"
            if cfg.auto_trim_silences:
                adjusted = 0
                for index, clip in enumerate(cfg.clips):
                    src = _source(clip.media_id)
                    pauses = await asyncio.to_thread(_silences, src)
                    info = await asyncio.to_thread(_probe, src)
                    start, end = _segment_bounds(clip, info, "B", pauses)
                    if start > clip.start + .05 or end < (clip.end or info["duration"]) - .05:
                        cfg.clips[index] = Clip(media_id=clip.media_id, start=start, end=end)
                        adjusted += 1
                if adjusted:
                    p["config"] = cfg.model_dump()
                    _save(p)
                    job["notes"].append(f"Автомонтаж: скорректировано сцен: {adjusted}")
            if job.get("cancel"):
                raise asyncio.CancelledError()
            job["stage"] = "2/4 · AI-субтитры (только если отсутствуют)"
            if cfg.auto_captions and not cfg.captions:
                try:
                    cfg.captions = [Word.model_validate(word) for word in await _transcribe_words(cfg)]
                    p["config"] = cfg.model_dump()
                    _save(p)
                    job["notes"].append(f"Автоматически распознано слов: {len(cfg.captions)}")
                except (RuntimeError, ImportError, OSError) as exc:
                    job["notes"].append("AI-субтитры недоступны: " + str(exc)[:180])
            if job.get("cancel"):
                raise asyncio.CancelledError()
            job["stage"] = "3/4 · Архив исходников и проверяемые отпечатки"
            try:
                await _analyze_project(p)
            except Exception as exc:
                job["notes"].append("Дополнительный анализ: " + str(exc)[:180])
            if job.get("cancel"):
                raise asyncio.CancelledError()
            job["stage"] = "4/4 · Начинаю создание MP4 A/B/C"
            for variant in ("A", "B", "C"):
                if job.get("cancel"):
                    raise asyncio.CancelledError()
                entry = await _render_variant(p, variant, job, work, "ABC".index(variant))
                p["exports"].append(entry)
                _save(p)
                _json_write(EXPORTS / p["id"] / (entry["id"] + "_manifest.json"), {
                    "project_id": p["id"], "template": p["template"],
                    "export": entry,
                })
                job["done"].append(entry["id"])
            job["status"] = "completed"
            job["progress"] = 100
            job["stage"] = "Готово: три файла для сравнения"
        except asyncio.CancelledError:
            job["status"] = "cancelled"
            job["stage"] = "Отменено; исходники и готовые экспорты сохранены"
        except Exception as exc:
            job["status"] = "error"
            job["error"] = str(exc)[-2000:]
        finally:
            shutil.rmtree(work, ignore_errors=True)
            job["finished_at"] = datetime.now(timezone.utc).isoformat()


@router.post("/projects/{project_id}/render")
async def start_render(project_id: str, request: Request):
    _local(request)
    p = _project(project_id)
    config = Config.model_validate(p["config"])
    if not config.clips or not config.background_name:
        raise HTTPException(400, "Сначала выбери кружки и фон")
    for mid in (x.media_id for x in config.clips):
        _source(mid)
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise HTTPException(503, "Нужны FFmpeg и FFprobe")
    if any(j["status"] in ("running", "queued") for j in JOBS.values()):
        raise HTTPException(409, "Уже идёт рендер Viral Reels Pro")
    ident = uuid.uuid4().hex
    repeated = _source_reuse(p)
    job = {"id": ident, "project_id": project_id, "status": "queued",
           "progress": 0, "stage": "Ожидание рендера",
           "cancel": False, "done": [],
           "notes": ([f"Внимание: похожие исходники встречались в {len(repeated)} других проектах"]
                     if repeated else []),
           "source_matches": repeated, "error": None}
    JOBS[project_id] = job
    task = asyncio.create_task(_job_runner(project_id, job))
    JOB_TASKS[project_id] = task
    return job


@router.post("/projects/{project_id}/preview")
async def render_preview(project_id: str, request: Request):
    """Render an actual short sample using the exact export compositor.

    The preview only includes the first selected scene. Originals and exported
    A/B/C files stay unchanged; the sample is not listed as a published export.
    """
    _local(request)
    project = _project(project_id)
    cfg = Config.model_validate(project["config"])
    if not cfg.clips or not _background(cfg):
        raise HTTPException(400, "Сначала выбери исходный кружок и фон")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise HTTPException(503, "Нужны FFmpeg и FFprobe")
    if RENDER_LOCK.locked() or any(
        j["status"] in ("running", "queued") for j in JOBS.values()
    ):
        raise HTTPException(409, "Сначала дождись окончания текущего рендера")
    first = cfg.clips[0]
    info = await asyncio.to_thread(_probe, _source(first.media_id))
    end = min(info["duration"], first.end if first.end is not None else info["duration"],
              first.start + 3.0)
    if end - first.start < .25:
        raise HTTPException(400, "Первый фрагмент слишком короткий для предпросмотра")
    sample = cfg.model_copy(deep=True)
    sample.clips = [Clip(media_id=first.media_id, start=first.start, end=end)]
    scratch_id = uuid.uuid4().hex
    scratch = {**project, "id": scratch_id, "config": sample.model_dump()}
    work = TMP / ("preview_" + scratch_id)
    job = {"id": scratch_id, "stage": "", "notes": [], "cancel": False, "progress": 0}
    async with RENDER_LOCK:
        work.mkdir(parents=True, exist_ok=True)
        try:
            rendered = await _render_variant(scratch, "A", job, work, 0)
            source = EXPORTS / scratch_id / rendered["file"]
            target = PREVIEWS / (project_id + ".mp4")
            os.replace(source, target)
        finally:
            shutil.rmtree(work, ignore_errors=True)
            shutil.rmtree(EXPORTS / scratch_id, ignore_errors=True)
    return {"ok": True, "duration": rendered["duration"],
            "message": "Реальный предпросмотр первых трёх секунд первой сцены",
            "url": f"/api/viral-pro/projects/{project_id}/preview"}


@router.get("/projects/{project_id}/preview")
def preview_video(project_id: str, request: Request):
    _local(request)
    _project(project_id)
    path = PREVIEWS / (_id(project_id) + ".mp4")
    if not path.is_file():
        raise HTTPException(404, "Сначала создай предпросмотр")
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "no-store"})


@router.get("/projects/{project_id}/job")
def job_status(project_id: str, request: Request):
    _local(request)
    _id(project_id)
    return JOBS.get(project_id, {"status": "idle", "progress": 0})


@router.post("/projects/{project_id}/cancel")
def cancel_render(project_id: str, request: Request):
    _local(request)
    job = JOBS.get(_id(project_id))
    if not job or job["status"] not in ("queued", "running"):
        raise HTTPException(409, "Активного рендера нет")
    job["cancel"] = True
    return {"ok": True, "message": "Отмена запрошена"}


@router.get("/projects/{project_id}/export/{export_id}")
def download_export(project_id: str, export_id: str, request: Request,
                    download: bool = False):
    _local(request)
    p = _project(project_id)
    entry = next((x for x in p["exports"] if x["id"] == _id(export_id)), None)
    if not entry:
        raise HTTPException(404, "Экспорт не найден")
    path = EXPORTS / project_id / entry["file"]
    if not path.is_file():
        raise HTTPException(404, "Файл экспорта отсутствует")
    return FileResponse(
        path, media_type="video/mp4", filename=path.name,
        content_disposition_type="attachment" if download else "inline",
    )


@router.get("/projects/{project_id}/export/{export_id}/manifest")
def download_export_manifest(project_id: str, export_id: str, request: Request):
    _local(request)
    p = _project(project_id)
    entry = next((e for e in p.get("exports", []) if e["id"] == _id(export_id)), None)
    if not entry:
        raise HTTPException(404, "Экспорт не найден")
    path = EXPORTS / p["id"] / (entry["id"] + "_manifest.json")
    if not path.is_file():
        # Backwards compatibility with previously rendered files.
        return {"project_id": p["id"], "template": p["template"], "export": entry}
    return FileResponse(path, media_type="application/json",
                        filename="viral_" + entry["id"][:10] + "_manifest.json")


@router.post("/projects/{project_id}/export/{export_id}/inbox")
def copy_export_to_inbox(project_id: str, export_id: str, request: Request):
    """Explicit user action: copy an already rendered variant to main queue."""
    _local(request)
    project = _project(project_id)
    entry = next(
        (e for e in project.get("exports", []) if e["id"] == _id(export_id)),
        None,
    )
    if not entry:
        raise HTTPException(404, "Экспорт не найден")
    source = EXPORTS / project_id / entry["file"]
    if not source.is_file():
        raise HTTPException(404, "Исходный MP4 экспорта отсутствует")
    dest = settings.upload_dir / f"viral_pro_{entry['variant']}_{export_id[:12]}.mp4"
    if not dest.exists():
        temporary = dest.with_suffix(".copying")
        try:
            shutil.copyfile(source, temporary)
            os.replace(temporary, dest)
        finally:
            temporary.unlink(missing_ok=True)
    return {"ok": True, "filename": dest.name,
            "message": "Готовый MP4 доступен в основной папке videos/inbox"}


def _source_reuse(project: dict) -> list[dict]:
    """Compare imported source fingerprints against other saved projects."""
    own_sources = project.get("analysis", {}).get("sources", [])
    if not own_sources:
        return []
    found = []
    for path in PROJECTS.glob("*.json"):
        if path.stem == project["id"]:
            continue
        try:
            other = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for source in other.get("analysis", {}).get("sources", []):
            for ours in own_sources:
                similarity = _similarity(ours, source)
                if similarity["exact"] or (similarity["visual"] or 0) >= .85:
                    found.append({
                        "other_project": other["id"],
                        "source_id": ours.get("media_id"),
                        "other_source_id": source.get("media_id"),
                        **similarity,
                    })
    return found[:100]


@router.get("/projects/{project_id}/duplicates")
def duplicates(project_id: str, request: Request):
    _local(request)
    p = _project(project_id)
    our = [e["fingerprint"] for e in p.get("exports", []) if e.get("fingerprint")]
    matches = []
    for path in PROJECTS.glob("*.json"):
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for entry in candidate.get("exports", []):
            if candidate["id"] == project_id:
                continue
            if not our:
                matches.append({"project_id": candidate["id"], "export_id": entry["id"],
                    "shared_sources": bool(set(entry.get("sources", [])) &
                                           set(x["media_id"] for x in p.get("analysis", {}).get("sources", [])))})
                continue
            sim = max((_similarity(fp, entry.get("fingerprint", {})) for fp in our),
                      key=lambda v: (v["exact"], v["visual"] or 0))
            if sim["exact"] or sim["audio_exact"] or (sim["visual"] or 0) > .78:
                matches.append({"project_id": candidate["id"],
                                "export_id": entry["id"], **sim,
                                "shared_sources": bool(set(entry.get("sources", [])) &
                                       set(x.media_id for x in Config.model_validate(p["config"]).clips))})
    return {
        "matches": matches[:100],
        "source_matches": _source_reuse(p),
        "note": (
            "dHash — эвристика визуального сходства; аудио SHA — точное "
            "совпадение PCM, не смысловая идентичность"
        ),
    }


@router.put("/projects/{project_id}/metrics")
def save_stats(project_id: str, data: Stats, request: Request):
    _local(request)
    p = _project(project_id)
    p["metrics"] = [m for m in p.get("metrics", []) if m["variant"] != data.variant]
    p["metrics"].append(data.model_dump())
    _save(p)
    return {"metrics": p["metrics"]}


@router.post("/projects/{project_id}/auto-edit")
async def auto_edit(project_id: str, request: Request):
    _local(request)
    p = _project(project_id)
    config = Config.model_validate(p["config"])
    suggestions = []
    for clip_index, clip in enumerate(config.clips):
        path = _source(clip.media_id)
        pauses = await asyncio.to_thread(_silences, path)
        info = _probe(path)
        beginning, ending = _segment_bounds(clip, info, "B", pauses)
        if beginning > clip.start + .05 or ending < (clip.end or info["duration"]) - .05:
            suggestions.append({
                "media_id": clip.media_id, "clip_index": clip_index,
                "original": clip.model_dump(),
                "suggested": {"media_id": clip.media_id, "start": beginning, "end": ending},
                "reason": "Паузы в начале/конце, можно сократить после проверки смысла",
            })
    return {"proposals": suggestions, "applied": False,
            "note": "Предложения обратимы: настройки исходного проекта не менялись"}


async def shutdown_viral_pro():
    for job in JOBS.values():
        if job["status"] in ("queued", "running"):
            job["cancel"] = True
    for task in list(JOB_TASKS.values()):
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
