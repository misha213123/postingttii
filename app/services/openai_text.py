from openai import OpenAI

from app.config import settings


def generate_caption(filename: str, hint: str = "") -> str:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY не заполнен в .env")

    client = OpenAI(api_key=settings.openai_api_key)

    prompt = f"""
Напиши описание для короткой стримерской нарезки.
Файл: {filename}
Контекст: {hint or "не указан"}

Формат:
- 3-5 коротких предложений на русском;
- живой стиль, как подпись под TikTok / Reels / Shorts;
- можно добавить 1 уместный эмодзи;
- не выдумывай имена, события, цитаты или факты, которых нет в контексте;
- последняя строка: 6-10 релевантных хештегов;
- используй стримерские/игровые хештеги;
- без заголовков, пояснений и кавычек.
""".strip()

    response = client.responses.create(
        model=settings.openai_model,
        input=prompt,
        reasoning={"effort": "minimal"},
        max_output_tokens=260,
        store=False,
    )
    return response.output_text.strip()
