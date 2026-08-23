"""Application settings, loaded from environment / backend/.env.

BUILD_SPEC Section 12: config.py reads .env via pydantic-settings.
Only the three variables named in .env.example are defined here — secrets never
appear in code, and the Razorpay keys are optional so the application always
boots on the built-in simulator alone (Section 5).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.enums import GatewayUsed


class Settings(BaseSettings):
    """Runtime configuration.

    Attributes:
        database_url: SQLAlchemy URL for the SQLite file (Docker-volume backed).
        razorpay_key_id: Razorpay TEST-mode key id. Optional — absent means the
            RazorpayTestGateway is unavailable and the toggle should be disabled
            in the UI, but the core loop is unaffected.
        razorpay_key_secret: Razorpay TEST-mode key secret. Optional, as above.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str = "sqlite:///./revora.db"
    razorpay_key_id: str | None = None
    razorpay_key_secret: str | None = None

    @property
    def razorpay_configured(self) -> bool:
        """True when both test-mode credentials are present.

        Session 5 uses this to decide whether RazorpayTestGateway can be
        constructed; the built-in simulator never consults it.
        """
        return bool(self.razorpay_key_id and self.razorpay_key_secret)

    @property
    def default_gateway(self) -> GatewayUsed:
        """Section 5: default is always the built-in simulator.

        This is the mode that must never fail during judging, so it is the
        default regardless of whether Razorpay credentials happen to be set.
        """
        return GatewayUsed.LOCAL_SIMULATION


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor, safe to use as a FastAPI dependency."""
    return Settings()
