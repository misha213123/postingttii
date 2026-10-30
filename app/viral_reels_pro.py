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
EXPORTS = ROOT / "exports"
TMP = ROOT / "temporary"
for d in (ROOT, PROJECTS, MEDIA, EXPORTS, TMP):
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
    background_name: str = ""
    banner_enabled: bool = False
    banner_x: int = Field(default=220, ge=-1080, le=1080)
    banner_y: int = Field(default=1500, ge=-1920, le=1920)
    banner_width: int = Field(default=600, ge=80, le=1080)
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
    if body.background_name:
        bg = BACKGROUNDS / Path(body.background_name).name
        if bg.name != body.background_name or not bg.is_file():
            raise HTTPException(400, "Выбранный фон отсутствует")
    if body.banner_enabled and not current_banner():
        raise HTTPException(400, "Сначала добавь баннер в Telegram Studio")
    if body.circle_x + body.circle_size > 1080 or body.circle_y + body.circle_size > 1920:
        raise HTTPException(400, "Кружок выходит за пределы кадра 1080×1920")
    p["config"] = body.model_dump()
    _save(p)
    return p


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
    findings = []
    fingerprints = []
    for i, clip in enumerate(config.clips):
        path = _source(clip.media_id)
        fp, silences = await asyncio.gather(
            asyncio.to_thread(fingerprint, path),
            asyncio.to_thread(_silences, path),
        )
        fingerprints.append({"media_id": clip.media_id, **fp})
        if silences:
            findings.append({"clip": i, "kind": "silence",
                             "message": f"Найдено {len(silences)} пауз речи.",
                             "intervals": silences[:30]})
        if fp["fps"] < 24:
            findings.append({"clip": i, "kind": "motion",
                             "message": "Низкий FPS: AUTO рассмотрит интерполяцию."})
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


def _make_ass(words: list[Word], temp: Path, config: Config, offset: float = 0):
    color = config.caption_color.lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", color):
        color = "FFFFFF"
    bgr = color[4:6] + color[2:4] + color[0:2]
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n"
            "[V4+ Styles]\n"
            "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,"
            "BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,"
            "BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
            f"Style: Viral,Arial,{config.caption_font_size},&H00{bgr},&H0000D9FF,"
            "&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,4,1,2,50,50,80,1\n"
            "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n")
    lines = []
    # Each spoken word is visibly displayed while it is active.
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
    def recognize():
        model = WhisperModel("small", device="cpu", compute_type="int8")
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
    p["config"]["captions"] = result
    _save(p)
    return {"words": result, "note": "AI-черновик: проверь текст и таймкоды вручную"}


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
            if pause["start"] <= start + .08 and pause["end"] <= start + 3:
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


async def _ffmpeg(job: dict, cmd: list[str], output: Path, estimated: float, index: int, total: int):
    logfile = TMP / (job["id"] + "_ffmpeg.log")
    try:
        with logfile.open("wb") as logs:
            process = subprocess.Popen(cmd, stderr=logs, stdout=subprocess.DEVNULL)
            begun = time.monotonic()
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
                # Estimate only; never claim this is precise FFmpeg progress.
                approximate = min(.95, (time.monotonic() - begun) / max(1, estimated))
                job["progress"] = round((index + approximate) / total * 100, 1)
                await asyncio.sleep(.5)
        if process.returncode:
            error = logfile.read_text(encoding="utf-8", errors="replace")[-1800:]
            output.unlink(missing_ok=True)
            raise RuntimeError(f"FFmpeg вернул {process.returncode}: {error}")
    finally:
        logfile.unlink(missing_ok=True)


async def _render_variant(project: dict, variant: str, job: dict, work: Path, position: int):
    config = Config.model_validate(project["config"])
    if not config.clips:
        raise ValueError("Нет исходных кружков")
    background = BACKGROUNDS / config.background_name if config.background_name else None
    if not background or not background.is_file():
        raise ValueError("Для Viral Reels Pro выбери фон из Telegram Studio")
    banner = current_banner() if config.banner_enabled else None
    if config.banner_enabled and not banner:
        raise ValueError("Рекламный баннер не найден")
    if config.smooth == "pro" and not "minterpolate" in _available_filters():
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
    total_steps = len(clips) * 3
    segs = []
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
        bgfilter = (
            f"[1:v]scale=1080:1920:force_original_aspect_ratio=increase:"
            f"flags=lanczos,crop=1080:1920,setsar=1,"
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
                "format=rgba,fade=t=in:st=0:d=0.3:alpha=1[ad];"
                f"[{prev}][ad]overlay={config.banner_x}:{config.banner_y}:"
                f"enable='between(t,{max(0,config.banner_start-offset)},{max(0,config.banner_end-offset)})'"
                "[withad];"
            )
            prev = "withad"
        graph += f"[{prev}]format=yuv420p[v]"
        # Subtitles are applied as a separate filter after composition,
        # mapping the same per-project word timeline to the current clip.
        words = [w for w in config.captions
                 if (offset <= w.start < offset + length)]
        if words:
            ass = work / f"sub_{variant}_{index}.ass"
            _make_ass(words, ass, config, offset)
            graph += f";[v]ass='{_ffmpeg_path(ass)}'[captioned]"
            videomap = "[captioned]"
        else:
            videomap = "[v]"
        cmd += ["-filter_complex", graph, "-map", videomap]
        if info["audio"]:
            cmd += ["-map", "0:a:0",
                    "-af", ("highpass=f=70,lowpass=f=12500,afftdn=nf=-25,"
                            "loudnorm=I=-16:TP=-1.5:LRA=11,alimiter=limit=0.95"
                            if config.loudness else "anull")]
        else:
            # Provide consistent audio for concat demuxer.
            cmd += ["-f", "lavfi", "-t", str(length), "-i", "anullsrc=r=48000:cl=stereo",
                    "-map", "3:a" if banner else "2:a"]
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
        await _ffmpeg(job, cmd, output, length * 2, position * total_steps + index,
                      3 * total_steps + 3)
        segs.append(output)
        offset += length
    if job.get("cancel"):
        raise asyncio.CancelledError()
    concat = work / f"{variant}_list.txt"
    concat.write_text("".join(f"file '{str(s).replace(chr(92), '/')}'\n" for s in segs),
                      encoding="utf-8")
    outdir = EXPORTS / project["id"]
    outdir.mkdir(exist_ok=True)
    export_id = uuid.uuid4().hex
    final = outdir / f"{export_id}_{variant}.mp4"
    job["stage"] = f"{variant}: склеиваю сцены и проверяю экспорт"
    await _ffmpeg(job, ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "concat", "-safe", "0", "-i", str(concat),
                        "-c", "copy", "-movflags", "+faststart", str(final)],
                  final, sum(x[3] for x in clips), 3 * total_steps - 3 + "ABC".index(variant), 3 * total_steps + 3)
    info = _probe(final)
    if info["width"] != 1080 or info["height"] != 1920:
        final.unlink(missing_ok=True)
        raise RuntimeError("Контроль 9:16: экспорт имеет неверные размеры")
    if abs(info["duration"] - sum(x[3] for x in clips)) > 1.1:
        final.unlink(missing_ok=True)
        raise RuntimeError("Контроль экспорта: рассинхронизация общей длительности")
    fp = fingerprint(final)
    return {
        "id": export_id, "variant": variant, "template": "viral_reels_pro_v3",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sha256": fp["sha256"], "fingerprint": fp,
        "sources": [x.media_id for x in config.clips],
        "file": final.name, "settings": config.model_dump(),
        "duration": info["duration"], "width": info["width"],
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
            job["stage"] = "Начинаю подготовку реальных MP4"
            for variant in ("A", "B", "C"):
                if job.get("cancel"):
                    raise asyncio.CancelledError()
                entry = await _render_variant(p, variant, job, work, "ABC".index(variant))
                p["exports"].append(entry)
                _save(p)
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
    job = {"id": ident, "project_id": project_id, "status": "queued",
           "progress": 0, "stage": "Ожидание рендера",
           "cancel": False, "done": [], "notes": [], "error": None}
    JOBS[project_id] = job
    task = asyncio.create_task(_job_runner(project_id, job))
    JOB_TASKS[project_id] = task
    return job


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
def download_export(project_id: str, export_id: str, request: Request):
    _local(request)
    p = _project(project_id)
    entry = next((x for x in p["exports"] if x["id"] == _id(export_id)), None)
    if not entry:
        raise HTTPException(404, "Экспорт не найден")
    path = EXPORTS / project_id / entry["file"]
    if not path.is_file():
        raise HTTPException(404, "Файл экспорта отсутствует")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


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
    return {"matches": matches[:100], "note":
            "dHash — эвристика визуального сходства; аудио SHA — точное совпадение PCM, не смысловая идентичность"}


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
    for clip in config.clips:
        path = _source(clip.media_id)
        pauses = await asyncio.to_thread(_silences, path)
        info = _probe(path)
        beginning, ending = _segment_bounds(clip, info, "B", pauses)
        if beginning > clip.start + .05 or ending < (clip.end or info["duration"]) - .05:
            suggestions.append({
                "media_id": clip.media_id,
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
