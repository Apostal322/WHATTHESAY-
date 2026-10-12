"""Environment-based configuration.

All settings can be supplied through environment variables or a `.env` file
(searched in the backend directory and the repository root). Secrets are never
given defaults.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_REPO_ROOT = _BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(_REPO_ROOT / ".env"), str(_BACKEND_DIR / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Provider credentials -------------------------------------------------
    gemini_api_key: SecretStr | None = Field(default=None, alias="GEMINI_API_KEY")

    # --- Models ---------------------------------------------------------------
    # Model IDs change frequently. Defaults reflect the current GA Flash model at
    # the time of writing; verify with `python scripts/list_models.py` and override.
    extraction_model: str = Field(default="gemini-3.8-flash", alias="YOUVERIFY_EXTRACTION_MODEL")
    verification_model: str = Field(
        default="gemini-3.8-flash", alias="YOUVERIFY_VERIFICATION_MODEL"
    )

    # --- Video window ---------------------------------------------------------
    # The user usually clicks *after* hearing the statement, so the window is
    # asymmetric: mostly before the click, a little after for sentence completion.
    window_before_sec: int = Field(default=45, ge=5, le=300, alias="YOUVERIFY_WINDOW_BEFORE_SEC")
    window_after_sec: int = Field(default=10, ge=0, le=120, alias="YOUVERIFY_WINDOW_AFTER_SEC")
    video_fps: float = Field(default=0.5, gt=0, le=24, alias="YOUVERIFY_VIDEO_FPS")
    video_media_resolution: Literal["low", "medium", "high"] = Field(
        default="low", alias="YOUVERIFY_VIDEO_MEDIA_RESOLUTION"
    )

    # --- Reliability ----------------------------------------------------------
    request_timeout_sec: float = Field(default=90, gt=0, le=600, alias="YOUVERIFY_TIMEOUT_SEC")
    max_retries: int = Field(default=2, ge=0, le=5, alias="YOUVERIFY_MAX_RETRIES")
    resolve_source_redirects: bool = Field(default=True, alias="YOUVERIFY_RESOLVE_REDIRECTS")

    # --- Abuse protection -----------------------------------------------------
    rate_limit_per_minute: int = Field(default=20, ge=1, alias="YOUVERIFY_RATE_LIMIT_PER_MINUTE")
    max_body_bytes: int = Field(default=16_384, ge=1024, alias="YOUVERIFY_MAX_BODY_BYTES")
    # Regex of allowed CORS origins. Restrict to your extension ID in practice:
    #   ^chrome-extension://<your-extension-id>$
    allowed_origin_regex: str = Field(
        default=r"^chrome-extension://[a-p]{32}$", alias="YOUVERIFY_ALLOWED_ORIGIN_REGEX"
    )

    # --- Caching --------------------------------------------------------------
    cache_ttl_sec: int = Field(default=3600, ge=0, alias="YOUVERIFY_CACHE_TTL_SEC")
    cache_max_entries: int = Field(default=512, ge=1, alias="YOUVERIFY_CACHE_MAX_ENTRIES")

    # --- Cost estimation (optional; USD per 1M tokens) -------------------------
    # Prices change; they are not hardcoded. When unset, cost is not estimated.
    price_input_per_million: float | None = Field(
        default=None, ge=0, alias="YOUVERIFY_PRICE_INPUT_PER_M"
    )
    price_output_per_million: float | None = Field(
        default=None, ge=0, alias="YOUVERIFY_PRICE_OUTPUT_PER_M"
    )
    # Google Search grounding on Gemini 3 is billed per executed search query.
    price_per_1k_search_queries: float | None = Field(
        default=None, ge=0, alias="YOUVERIFY_PRICE_PER_1K_SEARCHES"
    )

    log_level: str = Field(default="INFO", alias="YOUVERIFY_LOG_LEVEL")


@lru_cache
def get_settings() -> Settings:
    return Settings()
