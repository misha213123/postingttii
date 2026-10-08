from openai import OpenAI

from app.config import settings


BASE_HASHTAGS = "#おすすめ #リール #動画 #エンタメ #ゲーム #配信 #切り抜き"


def generate_caption(filename: str, hint: str = "") -> str:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY не заполнен в .env")

    client = OpenAI(api_key=settings.openai_api_key)

    prompt = f"""
日本語のみで、配信・ゲーム動画のInstagram Reels向けキャプションを書いてください。
Файл: {filename}
Контекст: {hint or "не указан"}

Формат:
- 日本語で自然な2〜3文。動画の内容に合う場合だけ具体的に書くこと。
- Instagram Reelsに合う自然で読みやすい文体。
- 絵文字は必要なら1〜2個まで。
- ファイル名とコンテキストにない人物名、出来事、セリフや事実を創作しない。
- 本文の後に改行し、日本語のハッシュタグを付ける。

必ず以下の基本ハッシュタグを含める：
{BASE_HASHTAGS}

内容が分かる場合だけ関連する日本語ハッシュタグを2〜4個追加する。日本語以外の文章やキリル文字、英語のハッシュタグは禁止。見出しや引用符は不要。
""".strip()

    response = client.responses.create(
        model=settings.openai_model,
        input=prompt,
        reasoning={"effort": "minimal"},
        max_output_tokens=220,
        store=False,
    )
    return response.output_text.strip()
