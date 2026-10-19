"""Regression tests for the second hardening round (H-20 onwards; docs/quality/defects.md)."""

from __future__ import annotations

from typing import Any

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
