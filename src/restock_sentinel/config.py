"""Application configuration, loaded from environment variables / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _str_to_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Config:
    """Runtime configuration for restock-sentinel.

    Loaded once from environment variables (populated from a .env file, if
    present, plus whatever is already in the process environment).
    """

    discord_webhook_url: str | None
    database_path: Path
    check_interval_seconds: int
    playwright_headless: bool
    enable_dry_run_checkout: bool

    @classmethod
    def load(cls, env_file: str | Path | None = ".env") -> Config:
        if env_file and Path(env_file).exists():
            load_dotenv(env_file)

        db_path = Path(os.getenv("DATABASE_PATH", "data/restock_sentinel.db"))
        db_path.parent.mkdir(parents=True, exist_ok=True)

        return cls(
            discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL") or None,
            database_path=db_path,
            check_interval_seconds=int(os.getenv("CHECK_INTERVAL_SECONDS", "60")),
            playwright_headless=_str_to_bool(os.getenv("PLAYWRIGHT_HEADLESS", "true")),
            enable_dry_run_checkout=_str_to_bool(
                os.getenv("ENABLE_DRY_RUN_CHECKOUT", "false")
            ),
        )
