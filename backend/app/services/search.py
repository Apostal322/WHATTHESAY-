"""Web search providers behind one interface, with ordered failover.

`SearchProvider` returns plain search hits (URL, title, snippet). Providers are
interchangeable: self-hosted SearXNG (primary candidate), the Brave Search API
(official, paid fallback) or test fakes. Credentials live only here, on the
backend; they are never sent to the extension.

`FailoverSearch` tries providers in order. A provider that fails repeatedly is
skipped for a cooldown period (a minimal circuit breaker), so a degraded
SearXNG instance, for example one whose upstream engines are rate-limiting or
blocking it, does not add its timeout to every request.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

import httpx

from app.core.logging import redact_secrets

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str | None
    snippet: str | None
    rank: int  # 1-based position in this provider's result list
    engines: tuple[str, ...] = ()  # upstream engines (SearXNG) that returned it


@dataclass(frozen=True)
class SearchResponse:
    hits: list[SearchHit]
    provider: str
    # Upstream engines that failed inside a meta-search (SearXNG "unresponsive_engines").
    degraded_engines: tuple[str, ...] = ()


class SearchProviderError(Exception):
    def __init__(self, provider: str, reason: str) -> None:
        super().__init__(f"{provider}: {reason}")
        self.provider = provider
        self.reason = reason


class SearchProvider(Protocol):
    name: str
    # USD per 1000 requests. 0.0 when there is no per-query fee (self-hosted SearXNG
    # costs infrastructure, not queries); None when a metered API's price is unknown.
    price_per_1k_requests: float | None

    async def search(self, query: str, *, language: str, max_results: int) -> SearchResponse: ...


def _lang(language: str) -> str | None:
    return None if language in ("", "auto") else language


async def _get_json(
    client: httpx.AsyncClient,
    provider: str,
    url: str,
    *,
    params: dict[str, str | int],
    headers: dict[str, str],
    timeout_sec: float,
) -> dict[str, object]:
    try:
        resp = await client.get(url, params=params, headers=headers, timeout=timeout_sec)
    except httpx.TimeoutException as exc:
        raise SearchProviderError(provider, "timeout") from exc
    except httpx.HTTPError as exc:
        raise SearchProviderError(provider, f"network:{type(exc).__name__}") from exc
    if resp.status_code == 429:
        raise SearchProviderError(provider, "rate_limited")
    if resp.status_code >= 400:
        raise SearchProviderError(provider, f"http_{resp.status_code}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise SearchProviderError(provider, "invalid_json") from exc
    if not isinstance(data, dict):
        raise SearchProviderError(provider, "invalid_json")
    return data


class SearxngProvider:
    """Self-hosted SearXNG (JSON output must be enabled: `search.formats: [html, json]`)."""

    name = "searxng"
    price_per_1k_requests: float | None = 0.0

    def __init__(self, base_url: str, client: httpx.AsyncClient, timeout_sec: float = 8.0) -> None:
        self._url = base_url.rstrip("/") + "/search"
        self._client = client
        self._timeout = timeout_sec

    async def search(self, query: str, *, language: str, max_results: int) -> SearchResponse:
        params: dict[str, str | int] = {"q": query, "format": "json", "safesearch": 1}
        if lang := _lang(language):
            params["language"] = lang
        data = await _get_json(
            self._client,
            self.name,
            self._url,
            params=params,
            headers={"Accept": "application/json"},
            timeout_sec=self._timeout,
        )
        raw = data.get("results")
        hits: list[SearchHit] = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict) or not isinstance(item.get("url"), str):
                continue
            engines = item.get("engines") or ([item["engine"]] if item.get("engine") else [])
            hits.append(
                SearchHit(
                    url=item["url"],
                    title=item.get("title") or None,
                    snippet=item.get("content") or None,
                    rank=len(hits) + 1,
                    engines=tuple(str(e) for e in engines),
                )
            )
            if len(hits) >= max_results:
                break
        unresponsive = data.get("unresponsive_engines")
        degraded = tuple(
            str(e[0])
            for e in (unresponsive if isinstance(unresponsive, list) else [])
            if isinstance(e, list) and e
        )
        return SearchResponse(hits=hits, provider=self.name, degraded_engines=degraded)


class BraveProvider:
    """Brave Search API (https://api.search.brave.com). The key never leaves the backend."""

    name = "brave"
    URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(
        self,
        api_key: str,
        client: httpx.AsyncClient,
        timeout_sec: float = 8.0,
        price_per_1k_requests: float | None = None,
    ) -> None:
        self._key = api_key
        self._client = client
        self._timeout = timeout_sec
        self.price_per_1k_requests = price_per_1k_requests

    async def search(self, query: str, *, language: str, max_results: int) -> SearchResponse:
        params: dict[str, str | int] = {"q": query, "count": min(max_results, 20)}
        if lang := _lang(language):
            params["search_lang"] = lang
        data = await _get_json(
            self._client,
            self.name,
            self.URL,
            params=params,
            headers={"Accept": "application/json", "X-Subscription-Token": self._key},
            timeout_sec=self._timeout,
        )
        web = data.get("web")
        raw = web.get("results") if isinstance(web, dict) else None
        hits: list[SearchHit] = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict) or not isinstance(item.get("url"), str):
                continue
            hits.append(
                SearchHit(
                    url=item["url"],
                    title=item.get("title") or None,
                    snippet=item.get("description") or None,
                    rank=len(hits) + 1,
                )
            )
        return SearchResponse(hits=hits[:max_results], provider=self.name)


class SpendGuard:
    """Daily spending cap for metered search APIs (resets at 00:00 UTC).

    Spend is reserved before a request and refunded if it fails, so concurrent
    requests cannot overshoot the cap. Per process: with several instances, give
    each a share of the budget (or move the counter to Redis, same interface).
    """

    def __init__(
        self, daily_limit_usd: float, now: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self.daily_limit_usd = daily_limit_usd
        self._now = now
        self._day = now().date()
        self.spent_usd = 0.0

    def _roll(self) -> None:
        today = self._now().date()
        if today != self._day:
            self._day, self.spent_usd = today, 0.0

    def try_reserve(self, usd: float) -> bool:
        self._roll()
        if self.spent_usd + usd > self.daily_limit_usd + 1e-12:
            return False
        self.spent_usd += usd
        return True

    def refund(self, usd: float) -> None:
        self._roll()
        self.spent_usd = max(0.0, self.spent_usd - usd)


@dataclass
class Attempt:
    provider: str
    ok: bool
    latency_ms: int
    hits: int = 0
    error: str | None = None


@dataclass
class FailoverOutcome:
    response: SearchResponse | None
    attempts: list[Attempt] = field(default_factory=list)


class FailoverSearch:
    """Try providers in order; skip a provider for `cooldown_sec` after repeated failures.

    An empty result counts as a soft failure (try the next provider) but does
    not trip the breaker, since some queries genuinely have no results.
    State is per process; a multi-instance deployment would share it (or rely
    on per-instance breakers, which is acceptable for this purpose).
    """

    def __init__(
        self,
        providers: list[SearchProvider],
        *,
        failure_threshold: int = 3,
        cooldown_sec: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        budget: SpendGuard | None = None,
    ) -> None:
        if not providers:
            raise ValueError("At least one search provider is required.")
        self.providers = providers
        self.budget = budget
        self._threshold = failure_threshold
        self._cooldown = cooldown_sec
        self._clock = clock
        self._failures: dict[str, int] = {}
        self._open_until: dict[str, float] = {}

    def _available(self, name: str) -> bool:
        return self._clock() >= self._open_until.get(name, 0.0)

    def _record(self, name: str, ok: bool) -> None:
        if ok:
            self._failures[name] = 0
            return
        self._failures[name] = self._failures.get(name, 0) + 1
        if self._failures[name] >= self._threshold:
            self._open_until[name] = self._clock() + self._cooldown
            self._failures[name] = 0
            log.warning("search_provider_cooldown", extra={"provider": name})

    async def search(self, query: str, *, language: str, max_results: int) -> FailoverOutcome:
        outcome = FailoverOutcome(response=None)
        for provider in self.providers:
            if not self._available(provider.name):
                outcome.attempts.append(Attempt(provider.name, False, 0, error="cooling_down"))
                continue
            fee = (provider.price_per_1k_requests or 0.0) / 1000
            if fee and self.budget is not None and not self.budget.try_reserve(fee):
                outcome.attempts.append(Attempt(provider.name, False, 0, error="budget_exhausted"))
                log.warning("search_budget_exhausted", extra={"provider": provider.name})
                continue
            started = time.perf_counter()
            try:
                resp = await provider.search(query, language=language, max_results=max_results)
            except SearchProviderError as exc:
                ms = int((time.perf_counter() - started) * 1000)
                if fee and self.budget is not None:
                    self.budget.refund(fee)  # failed requests are not billed
                self._record(provider.name, ok=False)
                reason = redact_secrets(exc.reason)
                outcome.attempts.append(Attempt(provider.name, False, ms, error=reason))
                log.warning(
                    "search_provider_error", extra={"provider": provider.name, "reason": reason}
                )
                continue
            ms = int((time.perf_counter() - started) * 1000)
            self._record(provider.name, ok=True)
            outcome.attempts.append(Attempt(provider.name, True, ms, hits=len(resp.hits)))
            if resp.hits:
                outcome.response = resp
                return outcome
        return outcome
