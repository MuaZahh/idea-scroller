"""Configuration loaded from environment variables."""

import platform
from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    anthropic_api_key: str = ""
    chrome_user_data_dir: str = ""
    comment_threshold: int = 300
    max_comments_per_video: int = 30
    max_videos: int = 0  # 0 = unlimited
    host: str = "127.0.0.1"
    port: int = 8000

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


def get_chrome_user_data_dir(settings: Settings) -> str:
    """Return Chrome user data directory, using platform default if not configured."""
    if settings.chrome_user_data_dir:
        return settings.chrome_user_data_dir

    system = platform.system()
    home = Path.home()

    if system == "Darwin":
        return str(home / "Library" / "Application Support" / "Google" / "Chrome")
    elif system == "Linux":
        return str(home / ".config" / "google-chrome")
    else:
        return str(home / "AppData" / "Local" / "Google" / "Chrome" / "User Data")
