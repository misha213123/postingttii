"""Instagram captions selected directly from a private local UTF-8 TXT file.

Each block separated by a standalone '---' line is one complete caption.
Nothing is translated, fabricated, prefixed or stripped from the template.
Place the file in DATA_DIR/instagram_caption_templates.txt (gitignored).
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from app.config import settings


class CaptionTemplatesError(ValueError):
    """The local Instagram caption template file is absent or invalid."""


def template_file() -> Path:
    configured = os.getenv("INSTAGRAM_CAPTION_TEMPLATES_FILE", "").strip().strip('"')
    if configured:
        path = Path(os.path.expandvars(configured)).expanduser()
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[2] / path
        return path.resolve()
    return (settings.data_dir / "instagram_caption_templates.txt").resolve()


def load_instagram_caption_templates() -> tuple[str, ...]:
    path = template_file()
    if not path.is_file():
        raise CaptionTemplatesError(
            f"Нет файла шаблонов Instagram: {path}. "
            "Скопируй приложенный TXT в data/instagram_caption_templates.txt."
        )
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise CaptionTemplatesError(f"Не удалось прочитать шаблоны Instagram: {exc}") from exc

    templates = tuple(
        block.strip()
        for block in re.split(r"(?m)^\\s*---\\s*$", raw)
        if block.strip()
    )
    if not templates:
        raise CaptionTemplatesError(f"Файл шаблонов Instagram пуст: {path}")
    if any(len(template) > 2200 for template in templates):
        raise CaptionTemplatesError(
            "Один из шаблонов Instagram превышает ограничение 2200 символов"
        )
    return templates


def instagram_caption_from_file(filename: str, slot: int = 1) -> str:
    """Choose a stable template for this video/account; return it verbatim."""
    templates = load_instagram_caption_templates()
    fingerprint = hashlib.sha256(f"{slot}\\0{filename}".encode("utf-8")).digest()
    index = int.from_bytes(fingerprint[:8], "big") % len(templates)
    return templates[index]
