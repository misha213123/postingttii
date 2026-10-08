"""Japanese/Chinese entertainment-style captions for Instagram Reels."""

from __future__ import annotations

import hashlib
import re

from openai import OpenAI

from app.config import settings


SHORT_JA = (
    "世界中の思いがけない瞬間をお届け！",
    "日常に隠れた不思議な出来事を紹介中！",
    "思わず見返したくなる珍しい瞬間を公開！",
    "予想外の展開と楽しい場面をお届け！",
)

LONG_JA = (
    "日常の中で起こる思いがけない出来事に注目！何気ない瞬間から生まれる驚きや笑い、予想外の展開を紹介します。さまざまな場面に隠された面白さをお楽しみください！",
    "世界中で見つかった印象的な瞬間を紹介！思わず二度見してしまう出来事や、意外な反応が生まれる場面を集めました。最後まで目が離せない展開をお楽しみください！",
)

LONG_ZH = (
    "生活中总有令人意想不到的精彩瞬间！看似平凡的场景里，往往藏着有趣的变化和出人意料的反应。一起发现这些值得回味的画面！",
    "来自日常生活的奇妙片段再次引起关注！意料之外的发展和真实有趣的反应，让普通的瞬间变得格外难忘。更多精彩画面持续分享中！",
)


def _fallback(filename: str) -> str:
    """Stable variation for each video, even without a useful filename."""
    digest = hashlib.sha256(filename.encode("utf-8")).digest()
    options = SHORT_JA + LONG_JA + LONG_ZH
    return options[int.from_bytes(digest[:4], "big") % len(options)]


def _clean_caption(text: str) -> str:
    # Captions must not contain hashtags, emoji or Latin letters.
    text = re.sub(r"#[^\s#]+", "", text)
    text = re.sub(r"[A-Za-zＡ-Ｚａ-ｚ]+", "", text)
    text = re.sub(r"[\U00010000-\U0010ffff\u2600-\u27bf]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(" \n\t#")


def generate_caption(filename: str, hint: str = "") -> str:
    """Produce an Asian-language caption; never add tags or unrelated brands."""
    if not settings.openai_api_key:
        return _fallback(filename)

    digest = hashlib.sha256(filename.encode("utf-8")).digest()
    style = ("short_ja", "long_ja", "long_zh")[digest[0] % 3]
    instructions = {
        "short_ja": "Одна короткая фраза на японском (15–40 символов).",
        "long_ja": "2–3 предложения на японском, около 90–150 символов.",
        "long_zh": "2–3 предложения на упрощённом китайском, около 80–130 иероглифов.",
    }[style]

    prompt = f"""Напиши подпись к развлекательному короткому видео.
Контекст пользователя: {hint or "не предоставлен"}.
Имя файла (только слабая подсказка, не достоверный факт): {filename}.

Стиль: как нейтральные японские или китайские развлекательные заметки.
{instructions}
Не заявляй, что видео связано с аниме, фильмом, брендом или знаменитостью,
если этого нет в контексте. Не выдумывай факты о содержании видео.
Если контекста недостаточно, используй универсальный текст про неожиданные моменты.
Никаких хештегов, эмодзи, латинских букв, английских слов, заголовков и пояснений.
Верни только готовую подпись."""

    try:
        client = OpenAI(api_key=settings.openai_api_key)
        response = client.responses.create(
            model=settings.openai_model,
            input=prompt,
            reasoning={"effort": "minimal"},
            max_output_tokens=450,
            store=False,
        )
        cleaned = _clean_caption(response.output_text or "")
        return cleaned if len(cleaned) >= 12 else _fallback(filename)
    except Exception:
        return _fallback(filename)
