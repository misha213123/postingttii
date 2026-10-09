"""TikTok captions through OpenAI, independent of Instagram's Japanese templates."""
from __future__ import annotations

from openai import OpenAI
from app.config import settings


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
    return result[:2200]
