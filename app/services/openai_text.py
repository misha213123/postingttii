from openai import OpenAI

from app.config import settings


def generate_caption(filename: str, hint: str = "") -> str:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY не заполнен в .env")

    client = OpenAI(api_key=settings.openai_api_key)

    prompt = f"""
Напиши короткое описание для стримерской нарезки.
Файл: {filename}
Контекст: {hint or "не указан"}

Формат:
- 1 короткая фраза по контексту, без выдуманных фактов;
- следующая строка: 6-10 релевантных стримерских/игровых хештегов;
- без заголовков и пояснений.
""".strip()

    response = client.responses.create(
        model=settings.openai_model,
        input=prompt,
        reasoning={"effort": "minimal"},
        max_output_tokens=160,
        store=False,
    )
    return response.output_text.strip()
