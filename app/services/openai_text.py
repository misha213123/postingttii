from openai import OpenAI

from app.config import settings


def generate_caption(filename: str, hint: str = "") -> str:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY не заполнен в .env")

    client = OpenAI(api_key=settings.openai_api_key)

    prompt = f"""
Ты пишешь подпись для короткого вертикального ролика со стримерской нарезкой.
Название файла: {filename}
Дополнительный контекст: {hint or "не указан"}

Сделай готовую подпись на русском:
1) 1-2 коротких предложения без выдуманных фактов, имен и событий.
2) Затем с новой строки 8-12 релевантных хештегов.
3) Используй стримерские/игровые теги, но не спамь одинаковыми тегами.
4) Не пиши пояснений, заголовков и кавычек.
""".strip()

    response = client.responses.create(
        model=settings.openai_model,
        input=prompt,
        store=False,
    )
    return response.output_text.strip()
