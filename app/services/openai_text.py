from openai import OpenAI

from app.config import settings


BASE_HASHTAGS = (
    "#стример #стрим #стримеры #нарезки #twitch #twitchclips "
    "#streamer #gaming #gamingclips #reels #shorts #viral"
)


def generate_caption(filename: str, hint: str = "") -> str:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY не заполнен в .env")

    client = OpenAI(api_key=settings.openai_api_key)

    prompt = f"""
Напиши короткое описание для стримерской нарезки.
Файл: {filename}
Контекст: {hint or "не указан"}

Формат:
- 1-2 коротких предложения на русском;
- живой стиль для TikTok / Reels / Shorts;
- можно 1 уместный эмодзи;
- не выдумывай имена, события, цитаты или факты;
- затем отдельной строкой хештеги.

Эти хештеги ВСЕГДА оставляй без изменений:
{BASE_HASHTAGS}

После них добавь еще 3-5 хештегов только по теме ролика, если контекст позволяет.
Не заменяй базовые хештеги и не переставляй их.
Без заголовков, пояснений и кавычек.
""".strip()

    response = client.responses.create(
        model=settings.openai_model,
        input=prompt,
        reasoning={"effort": "minimal"},
        max_output_tokens=220,
        store=False,
    )
    return response.output_text.strip()
