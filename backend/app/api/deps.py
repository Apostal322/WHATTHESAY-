"""Service wiring. Providers are created lazily so the app starts (and /health
works) without credentials; requests that need Gemini fail with a clear error."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
from fastapi import Request

from app.core.cache import TTLCache
from app.core.config import Settings
from app.core.errors import MissingCredentialsError
from app.core.rate_limit import SlidingWindowRateLimiter
from app.models.schemas import ExtractResponse, VerifyResponse
from app.services.extraction import ExtractionService
from app.services.llm import GeminiClient, LLMClient
from app.services.retrieval import Retriever, SearchRetriever
from app.services.search import (
    BraveProvider,
    FailoverOutcome,
    FailoverSearch,
    SearchProvider,
    SearxngProvider,
)
from app.services.verification import VerificationService


@dataclass
class AppState:
    settings: Settings
    http_client: httpx.AsyncClient
    rate_limiter: SlidingWindowRateLimiter
    llm: LLMClient | None = None
    retriever: Retriever | None = None
    extract_cache: TTLCache[ExtractResponse] = field(init=False)
    verify_cache: TTLCache[VerifyResponse] = field(init=False)
    search_cache: TTLCache[FailoverOutcome] = field(init=False)

    def __post_init__(self) -> None:
        s = self.settings
        self.extract_cache = TTLCache(s.cache_ttl_sec, s.cache_max_entries)
        self.verify_cache = TTLCache(s.cache_ttl_sec, s.cache_max_entries)
        self.search_cache = TTLCache(s.cache_ttl_sec, s.cache_max_entries)

    def get_llm(self) -> LLMClient:
        if self.llm is None:
            key = self.settings.gemini_api_key
            self.llm = GeminiClient(
                key.get_secret_value() if key else None,
                timeout_sec=self.settings.request_timeout_sec,
                max_retries=self.settings.max_retries,
            )
        return self.llm

    def get_retriever(self) -> Retriever | None:
        """None selects Gemini grounding (the VerificationService default)."""
        if self.settings.retrieval_backend != "search":
            return None
        if self.retriever is None:
            providers = build_search_providers(self.settings, self.http_client)
            if not providers:
                raise MissingCredentialsError(
                    "Search retrieval is enabled but no search provider is configured. Set "
                    "YOUVERIFY_SEARXNG_URL and/or BRAVE_SEARCH_API_KEY on the backend."
                )
            s = self.settings
            self.retriever = SearchRetriever(
                FailoverSearch(
                    providers,
                    failure_threshold=s.search_failure_threshold,
                    cooldown_sec=s.search_cooldown_sec,
                ),
                self.http_client,
                pages_to_fetch=s.pages_to_fetch,
                cache=self.search_cache,
            )
        return self.retriever


def build_search_providers(s: Settings, http: httpx.AsyncClient) -> list[SearchProvider]:
    """Configured providers in YOUVERIFY_SEARCH_PROVIDERS order; unconfigured ones are skipped."""
    providers: list[SearchProvider] = []
    for name in (n.strip().lower() for n in s.search_providers.split(",")):
        if name == "searxng" and s.searxng_url:
            providers.append(SearxngProvider(s.searxng_url, http, s.search_timeout_sec))
        elif name == "brave" and s.brave_api_key:
            providers.append(
                BraveProvider(
                    s.brave_api_key.get_secret_value(),
                    http,
                    s.search_timeout_sec,
                    s.price_per_1k_brave_requests,
                )
            )
    return providers


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
    return VerificationService(
        st.get_llm(), st.settings, st.http_client, st.verify_cache, st.get_retriever()
    )
