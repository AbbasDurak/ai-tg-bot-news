"""Ortam değişkenlerine dayalı ayarlar."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()


def _bool(value: Optional[str], default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "evet"}


def _int(value: Optional[str], default: int) -> int:
    try:
        return int(value) if value and value.strip() else default
    except ValueError:
        return default


def _hours(value: Optional[str]) -> list[int]:
    default = [8, 11, 14, 17, 20, 23]
    if not value:
        return default
    try:
        hours = sorted({int(h) for h in value.split(",") if h.strip()})
    except ValueError:
        return default
    hours = [h for h in hours if 0 <= h <= 23]
    return hours or default


class Settings(BaseModel):
    telegram_bot_token: str = Field(default="", repr=False)
    owner_id: Optional[int] = None
    groq_api_key: str = Field(default="", repr=False)
    groq_model: str = "llama-3.3-70b-versatile"
    db_path: str = "ai_radar.db"
    output_dir: str = "output"
    template_path: str = "assets/story_template.png"
    max_suggestions: int = 3
    max_item_age_days: int = 3
    timezone: str = "Europe/Istanbul"
    scan_hours: list[int] = [8, 11, 14, 17, 20, 23]
    log_level: str = "INFO"
    auto_publish_enabled: bool = False
    publish_chat_id: str = ""

    def validate_required(self) -> list[str]:
        problems = []
        if not self.telegram_bot_token:
            problems.append("TELEGRAM_BOT_TOKEN tanımlı değil")
        if self.owner_id is None:
            problems.append("TELEGRAM_OWNER_ID tanımlı değil veya sayı değil")
        if self.auto_publish_enabled and not self.publish_chat_id:
            problems.append("AUTO_PUBLISH_ENABLED=true için PUBLISH_CHAT_ID gerekli")
        return problems


def load_settings() -> Settings:
    env = os.environ.get
    owner_raw = (env("TELEGRAM_OWNER_ID") or "").strip()
    owner_id = int(owner_raw) if owner_raw.lstrip("-").isdigit() else None
    return Settings(
        telegram_bot_token=(env("TELEGRAM_BOT_TOKEN") or "").strip(),
        owner_id=owner_id,
        groq_api_key=(env("GROQ_API_KEY") or "").strip(),
        groq_model=(env("GROQ_MODEL") or "llama-3.3-70b-versatile").strip(),
        db_path=env("DB_PATH") or "ai_radar.db",
        output_dir=env("OUTPUT_DIR") or "output",
        template_path=env("STORY_TEMPLATE_PATH") or "assets/story_template.png",
        max_suggestions=_int(env("MAX_SUGGESTIONS_PER_SCAN"), 3),
        max_item_age_days=_int(env("MAX_ITEM_AGE_DAYS"), 3),
        timezone=env("TIMEZONE") or "Europe/Istanbul",
        scan_hours=_hours(env("SCAN_HOURS")),
        log_level=(env("LOG_LEVEL") or "INFO").upper(),
        auto_publish_enabled=_bool(env("AUTO_PUBLISH_ENABLED")),
        publish_chat_id=(env("PUBLISH_CHAT_ID") or "").strip(),
    )


def sanitize(text: str, settings: Optional[Settings] = None) -> str:
    """Loglara/hata mesajlarına sızabilecek sırları maskeler."""
    import re

    secrets = []
    if settings:
        secrets = [settings.telegram_bot_token, settings.groq_api_key]
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    text = re.sub(r"bot\d{5,}:[A-Za-z0-9_-]{20,}", "bot***", text)
    text = re.sub(r"gsk_[A-Za-z0-9]{10,}", "gsk_***", text)
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._-]+", r"\1***", text)
    return text


class SanitizingFilter:
    """logging.Filter: tüm log kayıtlarından sırları temizler."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def filter(self, record) -> bool:
        record.msg = sanitize(record.getMessage(), self.settings)
        record.args = ()
        return True


ASSETS_DIR = Path(__file__).parent / "assets"
