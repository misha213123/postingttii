"""OpenAI metadata generator exclusively for YouTube Shorts.

The model receives a filename and optional author context, not decoded video
frames. Never infer specific actions or people from an opaque filename.
"""
from __future__ import annotations

import json

from openai import OpenAI
from app.config import settings


def generate_youtube_metadata(filename: str, hint: str = "") -> dict[str, str]:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY не заполнен в .env (описания YouTube)")
    client = OpenAI(api_key=settings.openai_api_key)
    response = client.responses.create(
        model=settings.openai_model,
        input=(
            "Ты пишешь метаданные для YouTube Shorts. Ответь на русском языке. "
            "Создай короткое, естественное название до 90 символов и полезное "
            "описание 1–3 предложения с 3–5 релевантными хештегами, включая "
            "#shorts. Не придумывай подробности видео, содержание, имена, "
            "события или прямую речь: тебе доступны только имя файла и "
            "контекст от автора. При отсутствии информации используй "
            "нейтральное описание, не выдавай догадки за факты. "
            "Не включай хештеги в название. Не используй шаблоны Instagram.\n"
            f"Имя файла: {filename}\n"
            f"Контекст: {hint.strip() if hint.strip() else 'не указан'}"
        ),
        reasoning={"effort": "minimal"},
        text={
            "format": {
                "type": "json_schema",
                "name": "youtube_short_metadata",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["title", "description"],
                    "additionalProperties": False,
                },
            }
        },
        max_output_tokens=500,
        store=False,
    )
    try:
        parsed = json.loads(response.output_text)
        title = str(parsed["title"]).strip().replace("\n", " ")[:100]
        description = str(parsed["description"]).strip()[:5000]
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise RuntimeError("OpenAI не вернул корректные название и описание YouTube") from exc
    if not title or not description:
        raise RuntimeError("OpenAI вернул пустые метаданные YouTube")
    if "#shorts" not in description.lower():
        description = (description + " #shorts")[:5000]
    return {"title": title, "description": description}
