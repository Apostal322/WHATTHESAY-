"""Service wiring. Providers are created lazily so the app starts (and /health
works) without credentials; requests that need Gemini fail with a clear error."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
from fastapi import Request

from app.core.cache import TTLCache
from app.core.config import Settings
from app.core.rate_limit import SlidingWindowRateLimiter
from app.models.schemas import ExtractResponse, VerifyResponse
from app.services.extraction import ExtractionService
from app.services.llm import GeminiClient, LLMClient
from app.services.verification import VerificationService


@dataclass
class AppState:
    settings: Settings
    http_client: httpx.AsyncClient
    rate_limiter: SlidingWindowRateLimiter
    llm: LLMClient | None = None
    extract_cache: TTLCache[ExtractResponse] = field(init=False)
    verify_cache: TTLCache[VerifyResponse] = field(init=False)

    def __post_init__(self) -> None:
        s = self.settings
        self.extract_cache = TTLCache(s.cache_ttl_sec, s.cache_max_entries)
        self.verify_cache = TTLCache(s.cache_ttl_sec, s.cache_max_entries)

    def get_llm(self) -> LLMClient:
        if self.llm is None:
            key = self.settings.gemini_api_key
            self.llm = GeminiClient(
                key.get_secret_value() if key else None,
                timeout_sec=self.settings.request_timeout_sec,
                max_retries=self.settings.max_retries,
            )
        return self.llm


def get_state(request: Request) -> AppState:
    state: AppState = request.app.state.youverify
    return state


def rate_limit(request: Request) -> None:
    client = request.client.host if request.client else "unknown"
    get_state(request).rate_limiter.check(client)


def get_extraction_service(request: Request) -> ExtractionService:
    st = get_state(request)
    return ExtractionService(st.get_llm(), st.settings, st.extract_cache)


def get_verification_service(request: Request) -> VerificationService:
    st = get_state(request)
    return VerificationService(st.get_llm(), st.settings, st.http_client, st.verify_cache)
