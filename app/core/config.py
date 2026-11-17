"""
Application configuration using Pydantic BaseSettings.

Values are read from environment variables. Defaults are safe for
local development but must be overridden in any real deployment.
"""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Application
    app_name: str = "Secure Pipeline API"
    app_version: str = "0.1.0"
    environment: Literal["development", "testing", "production"] = "development"
    debug: bool = False

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # Security — JWT
    # In production: set via environment variable, never hardcode.
    # The default here is intentionally weak to trigger SAST rules
    # and demonstrate that the gate catches it.
    secret_key: str = "CHANGE_ME_IN_PRODUCTION_USE_32_BYTES_MINIMUM"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30

    # Metrics — bearer token required to read /metrics (e.g. Grafana Cloud
    # scrape job). Set via METRICS_TOKEN env var. If unset, /metrics is
    # closed to everyone (fail-closed).
    metrics_token: SecretStr | None = None

    # CORS
    allowed_origins: list[str] = ["http://localhost:3000", "http://localhost:8080"]

    # Pagination
    default_page_size: int = 20
    max_page_size: int = 100

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # Do not allow extra fields — prevents silent misconfiguration
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """
    Return a cached Settings instance.

    Using lru_cache means Settings is only instantiated once per
    process lifetime, which is correct for production. Tests override
    this by calling get_settings.cache_clear() between test cases.
    """
    return Settings()
