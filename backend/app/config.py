"""Application settings, read from environment variables (and a local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class Settings:
    database_url: str = ""
    cache_dir: Path = BACKEND_DIR / "data" / "cache"
    cors_origins: list[str] = field(default_factory=lambda: ["http://localhost:5173"])
    user_agent: str = "LeadFinderBot/0.1 (+mailto:ali.nasrullah@zarintech.se)"

    # SMTP / sender identity
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_security: str = "starttls"  # starttls | ssl | none
    smtp_from: str = "ali.nasrullah@zarintech.se"
    sender_name: str = "Ali Nasrullah"
    company_name: str = "Zarin Tech AB"
    company_org_number: str = ""
    sender_phone: str = ""

    # Throttling
    send_delay_min: int = 30
    send_delay_max: int = 60
    daily_cap: int = 30

    # Website enrichment
    enrich_delay_seconds: float = 1.0

    # Bolagsverket (Värdefulla datamängder)
    bolagsverket_client_id: str = ""
    bolagsverket_client_secret: str = ""
    bolagsverket_token_url: str = "https://gw.api.bolagsverket.se/oauth2/token"
    bolagsverket_base_url: str = "https://gw.api.bolagsverket.se/vardefulla-datamangder/v1"
    bolagsverket_scope: str = "vardefulla-datamangder:read"

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_password and self.smtp_from)

    @property
    def bolagsverket_configured(self) -> bool:
        return bool(self.bolagsverket_client_id and self.bolagsverket_client_secret)

    def missing_for_sending(self) -> list[str]:
        """Names of the settings that must be filled in before real emails can be sent."""
        missing = []
        if not self.smtp_host:
            missing.append("SMTP_HOST")
        if not self.smtp_user:
            missing.append("SMTP_USER")
        if not self.smtp_password:
            missing.append("SMTP_PASSWORD")
        if not self.company_org_number:
            missing.append("COMPANY_ORG_NUMBER")
        return missing


def load_settings() -> Settings:
    """Build Settings from the current environment (read fresh on every call)."""
    default_db = f"sqlite:///{BACKEND_DIR / 'data' / 'leadfinder.db'}"
    cors = [o.strip() for o in _str("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]
    s = Settings(
        database_url=_str("DATABASE_URL", default_db),
        cache_dir=Path(_str("CACHE_DIR", str(BACKEND_DIR / "data" / "cache"))),
        cors_origins=cors,
        user_agent=_str("USER_AGENT", Settings.user_agent),
        smtp_host=_str("SMTP_HOST"),
        smtp_port=_int("SMTP_PORT", 587),
        smtp_user=_str("SMTP_USER"),
        smtp_password=os.environ.get("SMTP_PASSWORD", ""),
        smtp_security=_str("SMTP_SECURITY", "starttls").lower(),
        smtp_from=_str("SMTP_FROM", "ali.nasrullah@zarintech.se"),
        sender_name=_str("SENDER_NAME", "Ali Nasrullah"),
        company_name=_str("COMPANY_NAME", "Zarin Tech AB"),
        company_org_number=_str("COMPANY_ORG_NUMBER"),
        sender_phone=_str("SENDER_PHONE"),
        send_delay_min=_int("SEND_DELAY_MIN", 30),
        send_delay_max=_int("SEND_DELAY_MAX", 60),
        daily_cap=_int("DAILY_CAP", 30),
        bolagsverket_client_id=_str("BOLAGSVERKET_CLIENT_ID"),
        bolagsverket_client_secret=_str("BOLAGSVERKET_CLIENT_SECRET"),
        bolagsverket_token_url=_str("BOLAGSVERKET_TOKEN_URL", Settings.bolagsverket_token_url),
        bolagsverket_base_url=_str("BOLAGSVERKET_BASE_URL", Settings.bolagsverket_base_url),
        bolagsverket_scope=_str("BOLAGSVERKET_SCOPE", Settings.bolagsverket_scope),
    )
    if s.send_delay_max < s.send_delay_min:
        s.send_delay_max = s.send_delay_min
    return s
