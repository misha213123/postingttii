from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv

load_dotenv()


def _path(name: str, default: str) -> Path:
    return Path(os.getenv(name, default)).resolve()


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("HOST", "127.0.0.1")
    port: int = int(os.getenv("PORT", "8765"))

    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5-nano")

    youtube_client_id: str = os.getenv("YOUTUBE_CLIENT_ID", "")
    youtube_client_secret: str = os.getenv("YOUTUBE_CLIENT_SECRET", "")
    youtube_redirect_uri: str = os.getenv(
        "YOUTUBE_REDIRECT_URI",
        "http://127.0.0.1:8765/auth/youtube/callback",
    )
    youtube_privacy_status: str = os.getenv("YOUTUBE_PRIVACY_STATUS", "public")

    tiktok_client_key: str = os.getenv("TIKTOK_CLIENT_KEY", "")
    tiktok_client_secret: str = os.getenv("TIKTOK_CLIENT_SECRET", "")
    tiktok_redirect_uri: str = os.getenv(
        "TIKTOK_REDIRECT_URI",
        "http://127.0.0.1:8765/auth/tiktok/callback",
    )
    tiktok_privacy_level: str = os.getenv("TIKTOK_PRIVACY_LEVEL", "SELF_ONLY")
    tiktok_enabled: bool = os.getenv("TIKTOK_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}

    # Anti-spam spacing between successful posts to the same account.
    # This is a safety/cadence control, not a guarantee against platform restrictions.
    post_cooldown_minutes: int = max(0, int(os.getenv("POST_COOLDOWN_MINUTES", "15")))

    instagram_client_id: str = os.getenv("INSTAGRAM_CLIENT_ID", "")
    instagram_client_secret: str = os.getenv("INSTAGRAM_CLIENT_SECRET", "")
    instagram_redirect_uri: str = os.getenv(
        "INSTAGRAM_REDIRECT_URI",
        "http://127.0.0.1:8765/auth/instagram/callback",
    )
    instagram_graph_version: str = os.getenv("INSTAGRAM_GRAPH_VERSION", "v25.0")
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

    upload_dir: Path = _path("UPLOAD_DIR", "videos/inbox")
    posted_dir: Path = _path("POSTED_DIR", "videos/posted")
    failed_dir: Path = _path("FAILED_DIR", "videos/failed")
    data_dir: Path = _path("DATA_DIR", "data")


settings = Settings()

for folder in (
    settings.upload_dir,
    settings.posted_dir,
    settings.failed_dir,
    settings.data_dir,
):
    folder.mkdir(parents=True, exist_ok=True)
