from __future__ import annotations

import hashlib
import html
import secrets
import string
from pathlib import Path

from app.config import settings


ADJECTIVES = (
    "Nova", "Pixel", "Neon", "Turbo", "Rapid", "Shadow", "Echo", "Cloud",
    "Night", "Hyper", "Prime", "Urban", "Zero", "Flux", "Orbit", "Frost",
)
NOUNS = (
    "Clips", "Moments", "Zone", "Pulse", "Frames", "Vault", "Arena", "Core",
    "Wave", "Rush", "Loop", "Lab", "Hub", "Byte", "Mode", "Spark",
)
BIOS = (
    "Короткие клипы, стримы и игровые моменты.",
    "Лучшие моменты со стримов — коротко и по делу.",
    "Gaming clips, реакции и яркие моменты.",
    "Нарезки стримов, фейлы, победы и смешные моменты.",
    "Короткие видео про игры, стримеров и интернет-моменты.",
)


def _slug(value: str) -> str:
    allowed = string.ascii_lowercase + string.digits + "_"
    value = value.lower().replace(" ", "_")
    return "".join(ch for ch in value if ch in allowed).strip("_")


def generate_creator_profile() -> dict[str, str]:
    adjective = secrets.choice(ADJECTIVES)
    noun = secrets.choice(NOUNS)
    display_name = f"{adjective} {noun}"
    suffix = secrets.randbelow(9000) + 1000
    username = f"{_slug(adjective)}{_slug(noun)}_{suffix}"
    return {
        "display_name": display_name,
        "username": username,
        "bio": secrets.choice(BIOS),
    }


def create_avatar_svg(account_id: int, display_name: str, username: str) -> Path:
    target_dir = settings.data_dir / "accounts" / str(account_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / "avatar.svg"

    words = [part for part in display_name.split() if part]
    initials = "".join(part[0] for part in words[:2]).upper() or username[:2].upper() or "AC"
    initials = html.escape(initials[:2])

    digest = hashlib.sha256(f"{account_id}:{username}".encode("utf-8")).digest()
    hue1 = int(digest[0] / 255 * 360)
    hue2 = (hue1 + 58 + int(digest[1] / 255 * 80)) % 360

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">
<defs>
  <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%" stop-color="hsl({hue1},72%,44%)"/>
    <stop offset="100%" stop-color="hsl({hue2},76%,28%)"/>
  </linearGradient>
</defs>
<rect width="512" height="512" rx="128" fill="url(#g)"/>
<circle cx="390" cy="118" r="92" fill="rgba(255,255,255,.08)"/>
<circle cx="110" cy="420" r="118" fill="rgba(0,0,0,.10)"/>
<text x="256" y="292" text-anchor="middle"
      font-family="Arial, Helvetica, sans-serif" font-size="176" font-weight="800"
      fill="#ffffff">{initials}</text>
</svg>"""
    path.write_text(svg, encoding="utf-8")
    return path
