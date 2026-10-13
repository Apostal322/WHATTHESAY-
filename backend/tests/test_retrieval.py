"""SearchRetriever end to end with fake providers and mocked publisher pages."""

from typing import Any

import httpx

from app.core.cache import TTLCache
from app.services.retrieval import SearchRetriever
from app.services.search import (
    FailoverOutcome,
    FailoverSearch,
    SearchHit,
    SearchProviderError,
    SearchResponse,
)

CLAIM = "The Eiffel Tower was completed in 1889."
QUERIES = ["Eiffel Tower completed 1889", "Eiffel Tower construction history"]

PAGES = {
    "www.toureiffel.paris": "<p>The Eiffel Tower was completed on 31 March 1889 after two years of construction.</p>",
    "en.wikipedia.org": "<p>Menu</p><p>Construction of the Eiffel Tower began in 1887 and the tower was completed in 1889.</p>",
    "www.history.example": "<p>Unrelated text about bakeries in Lyon and their bread recipes.</p>",
}
HITS = [
    SearchHit(
        "https://www.toureiffel.paris/en/history",
        "History | Eiffel Tower",
        "The tower was completed in 1889.",
        1,
    ),
    SearchHit(
        "https://en.wikipedia.org/wiki/Eiffel_Tower",
        "Eiffel Tower",
        "Eiffel Tower construction completed 1889",
        2,
    ),
    SearchHit(
        "https://www.history.example/eiffel",
        "Eiffel Tower facts",
        "Eiffel Tower completed 1889 facts",
        3,
    ),
    SearchHit(
        "https://down.example/eiffel",
        "Eiffel Tower 1889",
        "Eiffel Tower was completed in 1889 (snippet).",
        4,
    ),
]


def _pages(req: httpx.Request) -> httpx.Response:
    if req.url.host in PAGES:
        return httpx.Response(200, html=PAGES[req.url.host])
    return httpx.Response(503)


class Provider:
    def __init__(self, name: str, result: Any, price: float | None = 0.0) -> None:
        self.name = name
        self.price_per_1k_requests = price
        self._result = result
        self.calls = 0

    async def search(self, query: str, *, language: str, max_results: int) -> SearchResponse:
        self.calls += 1
        if isinstance(self._result, Exception):
            raise self._result
        return SearchResponse(hits=self._result, provider=self.name)


def _retriever(
    *providers: Any, cache: TTLCache[FailoverOutcome] | None = None, http: httpx.AsyncClient
) -> SearchRetriever:
    return SearchRetriever(FailoverSearch(list(providers)), http, cache=cache, check_dns=False)


async def test_page_text_sources_snippet_fallback_and_irrelevant_pages_dropped() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(_pages)) as http:
        res = await _retriever(Provider("searxng", HITS), http=http).retrieve(
            claim=CLAIM, queries=QUERIES, scope_notes=None, language="en"
        )
    by_domain = {s.domain: s for s in res.sources}
    assert by_domain["toureiffel.paris"].origin == "page_text"
    assert by_domain["toureiffel.paris"].excerpts == [
        "The Eiffel Tower was completed on 31 March 1889 after two years of construction."
    ]
    assert by_domain["en.wikipedia.org"].excerpts[0].startswith("Construction of the Eiffel Tower")
    assert by_domain["down.example"].origin == "search_snippet"  # fetch failed -> labelled snippet
    assert "history.example" not in by_domain  # page read, nothing relevant on it
    assert [s.id for s in res.sources] == [f"S{i}" for i in range(1, len(res.sources) + 1)]
    assert res.backend == "searxng"
    assert res.executed_queries == QUERIES
    assert res.billed_searches == 2 and res.search_cost_usd == 0.0
    assert "fetch_failed:http_503" in res.diagnostics["fetch"]
    assert "no_relevant_passage" in res.diagnostics["fetch"]


async def test_failover_to_priced_brave_is_costed_and_cache_hits_are_free() -> None:
    searx = Provider("searxng", SearchProviderError("searxng", "timeout"))
    brave = Provider("brave", HITS, price=5.0)
    cache: TTLCache[FailoverOutcome] = TTLCache(60, 10)
    async with httpx.AsyncClient(transport=httpx.MockTransport(_pages)) as http:
        retriever = _retriever(searx, brave, cache=cache, http=http)
        first = await retriever.retrieve(
            claim=CLAIM, queries=QUERIES, scope_notes=None, language="en"
        )
        second = await retriever.retrieve(
            claim=CLAIM, queries=QUERIES, scope_notes=None, language="en"
        )
    assert first.backend == "brave"
    assert first.billed_searches == 2 and first.search_cost_usd == 0.01
    assert second.billed_searches == 0 and second.search_cost_usd == 0.0
    assert brave.calls == 2  # second request served from cache
    assert all(q["cached"] for q in second.diagnostics["searches"])


async def test_unknown_price_makes_cost_unknown_not_zero() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(_pages)) as http:
        res = await _retriever(Provider("brave", HITS, price=None), http=http).retrieve(
            claim=CLAIM, queries=QUERIES, scope_notes=None, language="en"
        )
    assert res.search_cost_usd is None


async def test_all_providers_down_gives_no_sources_and_a_reason() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(_pages)) as http:
        res = await _retriever(
            Provider("searxng", SearchProviderError("searxng", "http_502")),
            Provider("brave", SearchProviderError("brave", "rate_limited")),
            http=http,
        ).retrieve(claim=CLAIM, queries=QUERIES, scope_notes=None, language="en")
    assert res.sources == [] and res.backend == "none"
    assert res.empty_reason == "All search providers failed or returned no results."
    assert res.billed_searches == 0
