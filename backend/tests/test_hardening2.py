"""Regression tests for the second hardening round (H-20 onwards; docs/quality/defects.md)."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from app.core.cache import TTLCache
from app.core.config import Settings
from app.models.schemas import ExtractRequest, ExtractResponse, VerifyRequest, VerifyResponse
from app.services.citations import EvidenceSource
from app.services.extraction import ExtractionService
from app.services.retrieval import RetrievalResult
from app.services.verification import VerificationService
from tests.conftest import ANALYSIS_FACTUAL, EXTRACTION_OK, FakeLLM

EVAL = {
    "source_assessments": [{"source_id": "S1", "stance": "supports", "note": "n"}],
    "verdict": "supported",
    "explanation": "Stated [S1].",
    "key_findings": [],
    "limitations": [],
}


class Retriever:
    name = "r"

    def __init__(self, sources: list[EvidenceSource], notices: list[str] | None = None) -> None:
        self.sources, self.notices, self.calls = sources, notices or [], 0

    async def retrieve(self, **kw: object) -> RetrievalResult:
        self.calls += 1
        return RetrievalResult(
            sources=self.sources,
            executed_queries=["q"],
            backend="r",
            notices=self.notices,
            degraded=bool(self.notices),
        )


def _src() -> EvidenceSource:
    return EvidenceSource("S1", "https://a.example.org/x", "A", "a.example.org",
                          ["The bridge opened in 2019."], origin="page_text")  # fmt: skip


def _llm() -> FakeLLM:
    return FakeLLM(
        json_responses={"ClaimAnalysisOutput": ANALYSIS_FACTUAL, "EvaluationOutput": EVAL}
    )


async def _twice(settings: Settings, retriever: Retriever, cache: TTLCache[Any], now: list[float],
                 gap: float = 0.0) -> None:  # fmt: skip
    svc = VerificationService(_llm(), settings, cache=cache, retriever=retriever)
    await svc.verify(VerifyRequest(claim="The bridge opened in 2019."))
    now[0] += gap
    await svc.verify(VerifyRequest(claim="The bridge opened in 2019."))


# ------------------------------------------------- H-20: degraded results are not cached long


async def test_h20_complete_result_is_cached(settings: Settings) -> None:
    now = [0.0]
    r = Retriever([_src()])
    await _twice(settings, r, TTLCache[VerifyResponse](3600, 10, clock=lambda: now[0]), now, 60)
    assert r.calls == 1


async def test_h20_result_from_partly_failed_search_is_not_cached(settings: Settings) -> None:
    now = [0.0]
    r = Retriever([_src()], notices=["1 of 2 searches failed (provider errors); ..."])
    await _twice(settings, r, TTLCache[VerifyResponse](3600, 10, clock=lambda: now[0]), now)
    assert r.calls == 2  # a transient outage must not shape answers for the next hour


async def test_h20_no_evidence_result_expires_quickly(settings: Settings) -> None:
    now = [0.0]
    r = Retriever([])
    cache = TTLCache[VerifyResponse](3600, 10, clock=lambda: now[0])
    await _twice(settings, r, cache, now, gap=60)
    assert r.calls == 1  # briefly cached: repeated clicks stay cheap
    now[0] += 600
    svc = VerificationService(_llm(), settings, cache=cache, retriever=r)
    await svc.verify(VerifyRequest(claim="The bridge opened in 2019."))
    assert r.calls == 2  # but "nothing found" is re-checked after minutes, not an hour


# -------------------------------------- H-21: extraction cache key covers how the clip was cut


async def test_h21_extraction_cache_distinguishes_window_settings(settings: Settings) -> None:
    llm = FakeLLM(json_responses={"ExtractionOutput": EXTRACTION_OK})
    cache = TTLCache[ExtractResponse](3600, 10)
    req = ExtractRequest(video_id="dQw4w9WgXcQ", timestamp_sec=100)
    await ExtractionService(llm, settings, cache).extract(req)
    wider = settings.model_copy(update={"window_before_sec": 90})
    await ExtractionService(llm, wider, cache).extract(req)
    assert len(llm.calls) == 2  # a different clip is a different extraction


# H-22 (when the claim was said) is tested in test_temporal.py.


# ------------------------------- H-28: a meta-search with failed engines is a partial result


async def test_h28_failed_upstream_engines_mark_retrieval_degraded_and_skip_caches() -> None:
    """Failed before: SearXNG's unresponsive engines were only logged; the result was complete
    as far as both caches knew."""
    import httpx

    from app.core.cache import TTLCache as Cache
    from app.services.retrieval import SearchRetriever
    from app.services.search import FailoverOutcome, FailoverSearch, SearchHit, SearchResponse

    class Searx:
        name, price_per_1k_requests, calls = "searxng", 0.0, 0

        async def search(self, query: str, *, language: str, max_results: int) -> SearchResponse:
            self.calls += 1
            hit = SearchHit("https://a.example.org/x", "Bridge", "The bridge opened in 2019.", 1)
            return SearchResponse([hit], "searxng", degraded_engines=("google", "bing"))

    page = "<p>The bridge opened in 2019 after four years of work.</p>"
    transport = httpx.MockTransport(lambda r: httpx.Response(200, html=page))
    provider, search_cache = Searx(), Cache[FailoverOutcome](3600, 10)
    async with httpx.AsyncClient(transport=transport) as http:
        retriever = SearchRetriever(
            FailoverSearch([provider]), http, cache=search_cache, check_dns=False
        )
        result = await retriever.retrieve(
            claim="The bridge opened in 2019.", queries=["bridge opened"], scope_notes=None,
            language="en",
        )  # fmt: skip
        assert result.degraded and any(
            "did not respond (bing, google)" in n for n in result.notices
        )
        await retriever.retrieve(
            claim="The bridge opened in 2019.", queries=["bridge opened"], scope_notes=None,
            language="en",
        )  # fmt: skip
    assert provider.calls == 2 and len(search_cache) == 0  # searched again, nothing stored


# ---------------------------- H-29: a fallback model's answer is not kept for the full TTL


class _FallbackLLM(FakeLLM):
    """Answers as the fallback model, as GeminiClient does when the configured one is overloaded."""

    async def generate_json(self, **kw: Any) -> Any:
        res = await super().generate_json(**kw)
        return dataclasses.replace(res, model="fallback-model")


async def test_h29_verification_served_by_fallback_model_expires_quickly(
    settings: Settings,
) -> None:
    now = [0.0]
    r = Retriever([_src()])
    cache = TTLCache[VerifyResponse](3600, 10, clock=lambda: now[0])
    llm = _FallbackLLM(
        json_responses={"ClaimAnalysisOutput": ANALYSIS_FACTUAL, "EvaluationOutput": EVAL}
    )
    svc = VerificationService(llm, settings, cache=cache, retriever=r)
    await svc.verify(VerifyRequest(claim="The bridge opened in 2019."))
    now[0] += 60
    await svc.verify(VerifyRequest(claim="The bridge opened in 2019."))
    assert r.calls == 1  # still reused briefly
    now[0] += 600
    await svc.verify(VerifyRequest(claim="The bridge opened in 2019."))
    assert r.calls == 2  # then the configured model gets another chance


async def test_h29_extraction_served_by_fallback_model_expires_quickly(settings: Settings) -> None:
    now = [0.0]
    llm = _FallbackLLM(json_responses={"ExtractionOutput": EXTRACTION_OK})
    cache = TTLCache[ExtractResponse](3600, 10, clock=lambda: now[0])
    svc = ExtractionService(llm, settings, cache)
    req = ExtractRequest(video_id="dQw4w9WgXcQ", timestamp_sec=100)
    await svc.extract(req)
    now[0] += 600
    await svc.extract(req)
    assert len(llm.calls) == 2


# --------------------------------------------------- cache review: the remaining guarantees


async def test_cache_no_sources_because_of_a_failure_is_not_cached(settings: Settings) -> None:
    r = Retriever([], notices=["2 of 2 result pages could not be read; ..."])  # degraded, empty
    await _twice(settings, r, TTLCache[VerifyResponse](3600, 10), [0.0])
    assert r.calls == 2  # a failure's "nothing found" is not "nothing exists"


async def test_cache_a_failed_retrieval_is_never_cached(settings: Settings) -> None:
    from app.core.errors import RetrievalUnavailableError

    class Down(Retriever):
        async def retrieve(self, **kw: object) -> RetrievalResult:
            self.calls += 1
            raise RetrievalUnavailableError("down")

    r, cache = Down([]), TTLCache[VerifyResponse](3600, 10)
    svc = VerificationService(_llm(), settings, cache=cache, retriever=r)
    for _ in range(2):
        with pytest.raises(RetrievalUnavailableError):
            await svc.verify(VerifyRequest(claim="The bridge opened in 2019."))
    assert r.calls == 2 and len(cache) == 0


async def test_cache_is_invalidated_by_model_policy_and_checker(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import verification

    r, cache = Retriever([_src()]), TTLCache[VerifyResponse](3600, 10)

    async def run(s: Settings, **kw: Any) -> None:
        await VerificationService(_llm(), s, cache=cache, retriever=r, **kw).verify(
            VerifyRequest(claim="The bridge opened in 2019.")
        )

    await run(settings)
    await run(settings)  # identical: served from the cache
    await run(settings.model_copy(update={"verification_model": "other-model"}))

    class OtherChecker:
        name = "nli"

        async def judge(self, *a: object) -> Any:  # pragma: no cover - not an attribution
            raise AssertionError

    await run(settings, entailment=OtherChecker())
    monkeypatch.setattr(verification, "VERIFICATION_POLICY_VERSION", "next")
    await run(settings)
    assert r.calls == 4


async def test_concurrent_requests_with_different_dates_are_not_coalesced(
    settings: Settings,
) -> None:
    import asyncio

    from app.core.singleflight import SingleFlight

    class Slow(Retriever):
        async def retrieve(self, **kw: object) -> RetrievalResult:
            await asyncio.sleep(0.01)
            return await super().retrieve(**kw)

    r = Slow([_src()])
    svc = VerificationService(_llm(), settings, retriever=r, flights=SingleFlight())
    same = VerifyRequest(claim="The bridge opened in 2019.", statement_date="2019")
    other = VerifyRequest(claim="The bridge opened in 2019.", statement_date="2015")
    await asyncio.gather(svc.verify(same), svc.verify(same), svc.verify(other))
    assert r.calls == 2  # the two identical ones shared a run; the other date did not


# ----------------------------------------- the exposed development key is refused in code


def test_a_revoked_key_is_refused_before_any_request(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.errors import ConfigurationError
    from app.services import llm

    fake = "fake-exposed-key-for-tests"  # secret-scan: allow (not a real key)
    monkeypatch.setattr(llm, "REVOKED_KEY_FINGERPRINTS", frozenset({llm.key_fingerprint(fake)}))
    with pytest.raises(ConfigurationError, match="exposed"):
        llm.GeminiClient(fake, timeout_sec=1, max_retries=0)
    llm.GeminiClient("another-fake-key", timeout_sec=1, max_retries=0)  # secret-scan: allow


def test_the_exposed_key_fingerprint_is_on_the_list() -> None:
    from app.services.llm import REVOKED_KEY_FINGERPRINTS

    assert "0ff24a559e0c" in REVOKED_KEY_FINGERPRINTS  # the key exposed on 2026-10-13


def test_api_answers_misconfigured_with_a_revoked_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.services import llm

    fake = "fake-exposed-key-for-tests"  # secret-scan: allow (not a real key)
    monkeypatch.setattr(llm, "REVOKED_KEY_FINGERPRINTS", frozenset({llm.key_fingerprint(fake)}))
    s = Settings(GEMINI_API_KEY=fake, _env_file=None)  # type: ignore[call-arg]
    with TestClient(create_app(s)) as client:
        r = client.post("/v1/claims/verify", json={"claim": "The bridge opened in 2019."})
    assert r.status_code == 503 and r.json()["error"]["code"] == "backend_misconfigured"
    assert "exposed" in r.json()["error"]["message"]
