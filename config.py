"""config.py - تنظیمات از متغیرهای محیطی (.env). هیچ رمزی داخل کد نیست."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_id: int
    support_username: str
    data_dir: str
    tz: str
    backup_passphrase: str
    backup_interval_hours: int
    default_max_live: int
    idle_timeout_min: int
    max_session_hours: int
    blocked_ips: frozenset
    brand: str = "AtronixX-Core"
    brand_header: str = "AtronixX-Core"

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "atronixx.db")

    @property
    def key_path(self) -> str:
        return os.path.join(self.data_dir, "secret.key")

    @property
    def tmp_dir(self) -> str:
        return os.path.join(self.data_dir, "tmp")

    @property
    def heartbeat_path(self) -> str:
        return "/tmp/atronixx.hb"

    @property
    def support_url(self) -> str:
        return "https://t.me/" + self.support_username.lstrip("@")


def load_settings() -> Settings:
    token = (os.getenv("BOT_TOKEN") or "").strip()
    if not re.fullmatch(r"\d{6,}:[\w-]{30,}", token):
        sys.exit("BOT_TOKEN is missing or invalid (check .env)")
    admin_id = _int("ADMIN_ID", 0)
    if admin_id <= 0:
        sys.exit("ADMIN_ID must be a numeric Telegram user id (check .env)")
    passphrase = (os.getenv("BACKUP_PASSPHRASE") or "").strip()
    if len(passphrase) < 8:
        sys.exit("BACKUP_PASSPHRASE must be at least 8 characters (check .env)")
    support = "@" + (os.getenv("SUPPORT_USERNAME") or "@AtronixX_Support").strip().lstrip("@")
    blocked = frozenset(x.strip() for x in (os.getenv("BLOCKED_IPS") or "").split(",") if x.strip())
    return Settings(
        bot_token=token,
        admin_id=admin_id,
        support_username=support,
        data_dir=os.getenv("DATA_DIR", "/data"),
        tz=os.getenv("TZ", "Asia/Tehran"),
        backup_passphrase=passphrase,
        backup_interval_hours=max(1, _int("BACKUP_INTERVAL_HOURS", 24)),
        default_max_live=max(1, _int("MAX_LIVE_SESSIONS", 20)),
        idle_timeout_min=max(2, _int("IDLE_TIMEOUT_MIN", 10)),
        max_session_hours=max(1, _int("MAX_SESSION_HOURS", 2)),
        blocked_ips=blocked,
    )


settings = load_settings()
