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
- ハッシュタグは一切書かない。

ハッシュタグと # 記号は禁止。日本語以外の文章は禁止。見出しや引用符は不要。
""".strip()

    response = client.responses.create(
        model=settings.openai_model,
        input=prompt,
        reasoning={"effort": "minimal"},
        max_output_tokens=220,
        store=False,
    )
    return response.output_text.strip()
