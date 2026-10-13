"""Search providers against a mocked HTTP transport, and failover behaviour."""

from typing import Any

import httpx
import pytest

from app.services.search import (
    BraveProvider,
    FailoverSearch,
    SearchHit,
    SearchProviderError,
    SearchResponse,
    SearxngProvider,
)

SEARX_BODY = {
    "results": [
        {
            "url": "https://www.bls.gov/ooh/x",
            "title": "BLS",
            "content": "Jobs grow.",
            "engines": ["bing", "duckduckgo"],
        },
        {"url": "https://example.org/a", "title": "A", "content": "", "engine": "brave"},
        {"title": "no url"},
    ],
    "unresponsive_engines": [["google", "CAPTCHA"], ["qwant", "timeout"]],
}


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_searxng_parses_results_and_reports_degraded_engines() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=SEARX_BODY)

    async with _client(handler) as http:
        resp = await SearxngProvider("http://searx.internal:8080/", http).search(
            "developer jobs", language="ru", max_results=5
        )
    assert seen[0].url.path == "/search"
    assert seen[0].url.params["format"] == "json"
    assert seen[0].url.params["language"] == "ru"
    assert [h.url for h in resp.hits] == ["https://www.bls.gov/ooh/x", "https://example.org/a"]
    assert resp.hits[0].engines == ("bing", "duckduckgo")
    assert resp.hits[1].engines == ("brave",) and resp.hits[1].snippet is None
    assert [h.rank for h in resp.hits] == [1, 2]
    assert resp.degraded_engines == ("google", "qwant")


async def test_searxng_auto_language_is_not_forced() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert "language" not in req.url.params
        return httpx.Response(200, json={"results": []})

    async with _client(handler) as http:
        resp = await SearxngProvider("http://s", http).search("q", language="auto", max_results=5)
    assert resp.hits == []


async def test_brave_sends_key_only_in_header_and_parses() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["X-Subscription-Token"] == "brave-secret"
        assert "brave-secret" not in str(req.url)
        assert req.url.params["search_lang"] == "en"
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {"url": "https://who.int/x", "title": "WHO", "description": "Facts."}
                    ]
                }
            },
        )

    async with _client(handler) as http:
        resp = await BraveProvider("brave-secret", http).search("q", language="en", max_results=5)
    assert resp.hits == [SearchHit("https://who.int/x", "WHO", "Facts.", 1)]


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(429), "rate_limited"),
        (httpx.Response(503), "http_503"),
        (httpx.Response(200, text="<html>not json</html>"), "invalid_json"),
        (httpx.Response(200, json=["not", "an", "object"]), "invalid_json"),
    ],
)
async def test_provider_errors_are_typed(response: httpx.Response, reason: str) -> None:
    async with _client(lambda req: response) as http:
        with pytest.raises(SearchProviderError) as info:
            await BraveProvider("brave-secret", http).search("q", language="en", max_results=5)
    assert info.value.reason == reason
    assert "brave-secret" not in str(info.value)


async def test_provider_timeout_is_typed() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=req)

    async with _client(handler) as http:
        with pytest.raises(SearchProviderError) as info:
            await SearxngProvider("http://s", http).search("q", language="en", max_results=5)
    assert info.value.reason == "timeout"


class FakeProvider:
    def __init__(self, name: str, results: list[Any]) -> None:
        self.name = name
        self.price_per_1k_requests: float | None = 0.0
        self._results = results  # consumed in order; Exception -> raised
        self.calls = 0

    async def search(self, query: str, *, language: str, max_results: int) -> SearchResponse:
        self.calls += 1
        item = self._results.pop(0) if len(self._results) > 1 else self._results[0]
        if isinstance(item, Exception):
            raise item
        return SearchResponse(hits=item, provider=self.name)


HIT = [SearchHit("https://a.org/x", "A", "s", 1)]


async def test_failover_uses_next_provider_on_error_and_on_empty() -> None:
    primary = FakeProvider("searxng", [SearchProviderError("searxng", "timeout"), []])
    backup = FakeProvider("brave", [HIT])
    search = FailoverSearch([primary, backup])

    first = await search.search("q", language="en", max_results=5)
    assert first.response is not None and first.response.provider == "brave"
    assert [(a.provider, a.ok, a.error) for a in first.attempts] == [
        ("searxng", False, "timeout"),
        ("brave", True, None),
    ]
    second = await search.search("q", language="en", max_results=5)  # primary returns no hits
    assert second.response is not None and second.response.provider == "brave"
    assert second.attempts[0].ok and second.attempts[0].hits == 0


async def test_breaker_skips_failing_provider_until_cooldown_ends() -> None:
    now = [0.0]
    primary = FakeProvider("searxng", [SearchProviderError("searxng", "http_502")])
    backup = FakeProvider("brave", [HIT])
    search = FailoverSearch(
        [primary, backup], failure_threshold=2, cooldown_sec=30, clock=lambda: now[0]
    )
    for _ in range(2):
        await search.search("q", language="en", max_results=5)
    assert primary.calls == 2

    out = await search.search("q", language="en", max_results=5)
    assert primary.calls == 2  # skipped: breaker open
    assert out.attempts[0].error == "cooling_down"

    now[0] = 31.0
    await search.search("q", language="en", max_results=5)
    assert primary.calls == 3  # retried after the cooldown


async def test_all_providers_failing_returns_no_response() -> None:
    search = FailoverSearch(
        [
            FakeProvider("searxng", [SearchProviderError("searxng", "timeout")]),
            FakeProvider("brave", [SearchProviderError("brave", "rate_limited")]),
        ]
    )
    out = await search.search("q", language="en", max_results=5)
    assert out.response is None
    assert [a.error for a in out.attempts] == ["timeout", "rate_limited"]


def test_failover_requires_a_provider() -> None:
    with pytest.raises(ValueError):
        FailoverSearch([])
