"""TikTok captions through OpenAI, independent of Instagram's Japanese templates."""
from __future__ import annotations

import re
from pathlib import Path

from openai import OpenAI
from app.config import settings


def clean_tiktok_caption(caption: str, filename: str) -> str:
    """Strip local filename metadata, including when glued to caption text."""
    text = (caption or "").strip()
    name = Path(filename).name
    stem = Path(filename).stem
    if not stem:
        return text

    # Imported technical identifiers may be glued to a word; ordinary
    # filenames must have a boundary so "fun.mp4" does not damage "funny".
    machine_label = bool(
        re.fullmatch(r"tg[_-][A-Za-z0-9_-]+", stem, flags=re.IGNORECASE)
    )
    prefix_pattern = (
        r"^\s*(?:(?:имя|название)\s+файла\s*[:\-]\s*)?"
        + re.escape(stem) + r"(?:\.mp4)?"
        + ("" if machine_label else r"(?=\W|$)")
    )
    text = re.sub(prefix_pattern, "", text, flags=re.IGNORECASE)
    text = re.sub(re.escape(name), "", text, flags=re.IGNORECASE)
    text = re.sub(
        r"(?<!\w)" + re.escape(stem) + r"(?!\w)",
        "", text, flags=re.IGNORECASE,
    )
    # Imported Telegram labels may be concatenated even in the middle.
    if machine_label:
        text = re.sub(re.escape(stem), "", text, flags=re.IGNORECASE)

    text = re.sub(r"\s+", " ", text).lstrip(" :—–|_,.;-").strip()
    return text or "Короткое видео #видео"


def generate_tiktok_caption(filename: str, hint: str = "") -> str:
    """Generate a short TikTok caption; no Instagram caption code is changed.

    The model sees only a filename and optional manual context, not video frames.
    Do not pretend to have analyzed the video.
    """
    if not settings.openai_api_key:
        raise RuntimeError("Для TikTok заполни OPENAI_API_KEY в .env")

    client = OpenAI(api_key=settings.openai_api_key)
    prompt = (
        "Напиши одно короткое естественное описание для TikTok на русском языке, "
        "не более 130 символов основного текста плюс 2–4 тематических хештега. "
        "Если тема видео неизвестна, используй нейтральную формулировку, "
        "без утверждений о сюжете, людях, репликах или событиях, которые ты не видел. "
        "Никаких придуманых фактов, рекламных обещаний или кликбейта. "
        "Не добавляй кавычки, пояснения, названия разделов. "
        "Название файла — только служебная подсказка: не включай его, его части, "
        "расширение .mp4 и технические идентификаторы в текст публикации. "
        "Ты не смотришь сам файл, а знаешь только его имя и переданный контекст.\n"
        f"Имя файла: {filename}\n"
        f"Контекст автора: {hint.strip() if hint.strip() else 'отсутствует'}"
    )
    response = client.responses.create(
        model=settings.openai_model,
        input=prompt,
        reasoning={"effort": "minimal"},
        max_output_tokens=200,
        store=False,
    )
    result = (response.output_text or "").strip()
    if not result:
        raise RuntimeError("OpenAI вернул пустое описание TikTok")
    return clean_tiktok_caption(result, filename)[:2200]
