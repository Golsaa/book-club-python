from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()


GENRES = ("Fiction", "Non-Fiction", "Mystery", "Sci-Fi", "Fantasy")


def _as_bool(value: str, default: bool = False) -> bool:
    return value.lower() in {"1", "true", "yes", "on"} if value else default


@dataclass(frozen=True)
class Settings:
    session_secret: str
    database_url: str
    google_client_id: str
    google_client_secret: str
    google_redirect_uri: str
    cookie_secure: bool
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_from: str
    smtp_use_tls: bool
    voting_first_start: datetime
    voting_period_days: int

    @classmethod
    def from_environment(cls) -> "Settings":
        first_start = datetime.fromisoformat(
            os.getenv("VOTING_FIRST_START", "2026-11-01T00:00:00+00:00")
        )
        if first_start.tzinfo is None:
            first_start = first_start.replace(tzinfo=timezone.utc)
        first_start = first_start.astimezone(timezone.utc)
        return cls(
            session_secret=os.getenv("SESSION_SECRET", "local-only-change-me"),
            database_url=os.getenv("DATABASE_URL", "sqlite:///./data/bookclub.db"),
            google_client_id=os.getenv("GOOGLE_CLIENT_ID", ""),
            google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET", ""),
            google_redirect_uri=os.getenv(
                "GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/callback"
            ),
            cookie_secure=_as_bool(os.getenv("COOKIE_SECURE", "false")),
            smtp_host=os.getenv("SMTP_HOST", ""),
            smtp_port=int(os.getenv("SMTP_PORT", "587")),
            smtp_username=os.getenv("SMTP_USERNAME", ""),
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            smtp_from=os.getenv("SMTP_FROM", ""),
            smtp_use_tls=_as_bool(os.getenv("SMTP_USE_TLS", "true"), True),
            voting_first_start=first_start,
            voting_period_days=max(1, int(os.getenv("VOTING_PERIOD_DAYS", "14"))),
        )


settings = Settings.from_environment()