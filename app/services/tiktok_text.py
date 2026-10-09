"""TikTok caption generation using visible MP4 frames, never filenames.

Instagram captions and YouTube metadata remain independent. Only three
compressed frames (not the whole video or its audio) are sent to the configured
OpenAI API. This is visual context, not a claim to have watched/listened to
every second of the source video.
"""
from __future__ import annotations

import base64
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

from openai import OpenAI

from app.config import settings


_BAD_PHRASES = (
    "нейтральное короткое описание",
    "неизвестной темой",
    "тема видео неизвестна",
    "тема неизвестна",
    "без домыслов",
    "без реклам",
    "имя файла",
    "название файла",
    "не имею доступа к видео",
    "не могу просмотреть видео",
    "я не вижу видео",
    "не вижу само видео",
    "описание для видео",
    "хештеги:",
    "контекст автора:",
)
_FILE_STEM = re.compile(r"(?i)\b(?:ig|tg)_[a-z0-9_]{10,}\b")


def caption_needs_regeneration(caption: str, filename: str = "") -> bool:
    """Identify technical captions that should never reach a TikTok upload."""
    text = re.sub(r"\s+", " ", caption or "").strip()
    lower = text.casefold()
    stem = Path(filename).stem.casefold() if filename else ""
    if not text or len(text) < 15:
        return True
    if stem and stem in lower:
        return True
    if _FILE_STEM.search(text):
        return True
    if any(phrase in lower for phrase in _BAD_PHRASES):
        return True
    if text.startswith(("{" , "[", "Описание:", "Подпись:")):
        return True
    if len(text) > 450:
        return True
    return False


def _video_frames(video: Path) -> list[str]:
    """Return data-URL JPEGs sampled at 20, 50 and 80 percent of the video."""
    video = Path(video)
    if not video.is_file():
        raise RuntimeError("TikTok: видео не найдено для создания описания")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("TikTok: ffmpeg/ffprobe не найдены. Нужны для анализа кадров")

    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
            capture_output=True, text=True, check=True, timeout=30,
        )
        duration = float(probe.stdout.strip())
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("TikTok: не удалось определить длительность MP4") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError("TikTok: некорректная длительность MP4")

    frames: list[str] = []
    for ratio in (0.2, 0.5, 0.8):
        at = min(max(duration * ratio, 0.0), max(0.0, duration - 0.1))
        cmd = [
            "ffmpeg", "-v", "error", "-ss", f"{at:.3f}", "-i", str(video),
            "-frames:v", "1", "-vf",
            "scale=576:-2:force_original_aspect_ratio=decrease",
            "-q:v", "5", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1",
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, check=True, timeout=45,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("TikTok: не удалось получить кадры из MP4") from exc
        if not result.stdout or len(result.stdout) > 1_500_000:
            raise RuntimeError("TikTok: не получен корректный кадр MP4")
        frames.append(
            "data:image/jpeg;base64," + base64.b64encode(result.stdout).decode("ascii")
        )
    return frames


def generate_tiktok_caption(filename: str | Path, hint: str = "") -> str:
    """Describe what is visible in sampled frames, not a technical filename."""
    if not settings.openai_api_key:
        raise RuntimeError("Для TikTok заполни OPENAI_API_KEY в .env")
    frames = _video_frames(Path(filename))
    if len(frames) < 2:
        raise RuntimeError("TikTok: слишком мало кадров для описания видео")

    guidance = (
        "Ты редактор подписей для TikTok. К сообщению приложены 3 кадра "
        "одного ролика, извлечённые из разных моментов видео. "
        "На основе только того, что реально видно на кадрах, составь "
        "ЕСТЕСТВЕННУЮ короткую подпись на русском языке: одна фраза "
        "или 1–2 коротких предложения и 2–4 релевантных хештега. "
        "Выбирай тему по кадрам; если она неясна, используй короткую "
        "вовлекающую подпись, подходящую для зрителей. Не пиши о том, "
        "что тема неизвестна, что у тебя недостаточно информации, "
        "и не пересказывай эту инструкцию. Не включай техническое "
        "имя файла, платформенные названия Instagram, нейросеть, "
        "общие фразы про описание видео или слово 'хештеги'. "
        "Не придумывай диалоги, звуки, имена, события вне кадров. "
        "Отвечай одним JSON-объектом с полем caption. "
        + (f"Дополнительный контекст автора: {hint.strip()[:600]}" if hint.strip() else "")
    )
    content: list[dict] = [{"type": "input_text", "text": guidance}]
    content.extend({"type": "input_image", "image_url": frame, "detail": "low"} for frame in frames)
    client = OpenAI(api_key=settings.openai_api_key)

    for attempt in range(2):
        response = client.responses.create(
            model=settings.openai_model,
            input=[{"role": "user", "content": content}],
            reasoning={"effort": "minimal"},
            text={
                "format": {
                    "type": "json_schema",
                    "name": "tiktok_caption",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {"caption": {"type": "string"}},
                        "required": ["caption"],
                        "additionalProperties": False,
                    },
                }
            },
            max_output_tokens=360,
            store=False,
        )
        try:
            payload = json.loads(response.output_text or "{}")
            caption = re.sub(r"\s+", " ", str(payload["caption"])).strip()
        except (ValueError, TypeError, KeyError):
            caption = ""
        hashtags = re.findall(r"(?<!\w)#[\wа-яА-ЯёЁ]+", caption)
        if (
            not caption_needs_regeneration(caption, Path(filename).name)
            and 2 <= len(hashtags) <= 4
        ):
            return caption

        if attempt == 0:
            content.append({
                "type": "input_text",
                "text": (
                    "Прошлый ответ нельзя публиковать: он был техническим или "
                    "без нужных хештегов. Напиши новый естественный текст для "
                    "зрителя с 2–4 хештегами, без имени файла или инструкций."
                ),
            })
    raise RuntimeError(
        "TikTok: OpenAI дважды вернул неподходящую подпись. "
        "Публикация отменена, чтобы не выкладывать технический текст."
    )
