"""When was it said? (H-22 and its correction, H-26.)

H-22 sent the YouTube upload date as the statement date and told the evaluator to judge the claim
as of that date. Archival footage, compilations and rebroadcasts are published years after the
words were said, so that judged them against the wrong period. Now the upload date is only an
upper bound, the statement date is used only when the video itself (or the user) gives it, and
conflicting or impossible dates are dropped and reported, never guessed.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.core.cache import TTLCache
from app.core.config import Settings
from app.models.llm_outputs import ExtractionOutput
from app.models.schemas import TimeWindow, TokenUsage, VerifyRequest, VerifyResponse
from app.services.citations import EvidenceSource, format_sources_for_prompt
from app.services.extraction import build_extract_response
from app.services.fetch import fetch_page
from app.services.prompts import EXTRACTION_SYSTEM, extraction_prompt
from app.services.retrieval import RetrievalResult, SearchRetriever
from app.services.search import BraveProvider, FailoverSearch, SearxngProvider
from app.services.temporal import page_published_date, temporal_context
from app.services.verification import VerificationService
from tests.conftest import ANALYSIS_FACTUAL, FakeLLM

TODAY = date(2026, 10, 19)
CLAIM = "Unemployment is at a record low."
EVAL = {
    "source_assessments": [{"source_id": "S1", "stance": "supports", "note": "n"}],
    "verdict": "supported",
    "explanation": "Stated [S1].",
    "key_findings": [],
    "limitations": [],
}


class Retriever:
    name = "r"

    def __init__(self) -> None:
        self.calls = 0

    async def retrieve(self, **kw: object) -> RetrievalResult:
        self.calls += 1
        src = EvidenceSource("S1", "https://a.example.org/x", "A", "a.example.org",
                             ["Unemployment fell to a record low."], origin="page_text")  # fmt: skip
        return RetrievalResult(sources=[src], executed_queries=["q"], backend="r")


async def prompts_for(settings: Settings, **dates: Any) -> tuple[str, str, VerifyResponse]:
    llm = FakeLLM(
        json_responses={"ClaimAnalysisOutput": ANALYSIS_FACTUAL, "EvaluationOutput": EVAL}
    )
    resp = await VerificationService(llm, settings, retriever=Retriever()).verify(
        VerifyRequest(claim=CLAIM, **dates)
    )
    by = {name: str(kw["prompt"]) for name, kw in llm.calls}
    return by["ClaimAnalysisOutput"], by["EvaluationOutput"], resp


# ------------------------------------------------------------------- what the model is told


async def test_old_footage_uploaded_recently_is_not_judged_as_of_the_upload_date(
    settings: Settings,
) -> None:
    """Failed before (H-22): the upload date was sent as the statement date, and the prompt said
    "Judge it as of that date"."""
    analysis, evaluation, _ = await prompts_for(settings, video_published_date="2024-05-01")
    for prompt in (analysis, evaluation):  # search planning and the verdict both see it
        assert "When it was said: unknown" in prompt
        assert "on or before that date, possibly years earlier" in prompt
        assert "Do not assume it was said on the publication date" in prompt
        assert "Judge the claim as of" not in prompt
    assert "prefer mixed or insufficient_evidence" in evaluation
    assert "prefer mixed" not in analysis  # search planning gets the facts, not verdict rules


async def test_a_date_shown_in_the_video_is_used_with_the_upload_date_as_bound(
    settings: Settings,
) -> None:
    analysis, evaluation, _ = await prompts_for(
        settings, statement_date="1998", video_published_date="2024-05-01"
    )
    for prompt in (analysis, evaluation):
        assert "When it was said: 1998" in prompt and "published on 2024-05-01" in prompt
    assert "Judge the claim as of that time" in evaluation


async def test_rebroadcast_years_after_the_recording_keeps_the_recording_date(
    settings: Settings,
) -> None:
    _, evaluation, resp = await prompts_for(
        settings, statement_date="2011-03-14", video_published_date="2026-10-01"
    )
    assert "When it was said: 2011-03-14" in evaluation
    assert not any("conflict" in n for n in resp.guardrail_notes)


@pytest.mark.parametrize(
    ("dates", "note"),
    [
        ({"statement_date": "2025-06", "video_published_date": "2024-05-01"}, "conflict"),
        ({"statement_date": "2031", "video_published_date": "2024-05-01"}, "in the future"),
        ({"video_published_date": "2031-01-01"}, "in the future"),
    ],
)
async def test_conflicting_or_impossible_dates_become_unknown_and_are_reported(
    settings: Settings, dates: dict[str, str], note: str
) -> None:
    _, evaluation, resp = await prompts_for(settings, **dates)
    assert "Judge the claim as of" not in evaluation  # never judged against a guessed date
    assert any(note in n for n in resp.guardrail_notes)


async def test_without_any_date_nothing_is_assumed(settings: Settings) -> None:
    analysis, evaluation, _ = await prompts_for(settings)
    assert "When it was said" not in analysis + evaluation


def test_temporal_context_rules() -> None:
    assert temporal_context("1998-05", "1998-05-01", TODAY).said_on == "1998-05"  # same month
    assert temporal_context("1998-05-02", "1998-05-01", TODAY).said_on is None  # after upload
    assert temporal_context(None, "2026-10-20", TODAY).said_by == "2026-10-20"  # a day of skew
    assert temporal_context(None, "2026-10-21", TODAY).said_by is None


def test_request_dates_are_validated() -> None:
    for ok in ("1998", "1998-05", "1998-05-12"):
        assert VerifyRequest(claim=CLAIM, statement_date=ok).statement_date == ok
    for bad in ("1998-13", "1998-02-30", "12.05.1998", "1998-05-12T10:00", "yesterday"):
        with pytest.raises(ValidationError):
            VerifyRequest(claim=CLAIM, statement_date=bad)
    for bad in ("2024", "2024-02-30"):  # the upload date is always a full day
        with pytest.raises(ValidationError):
            VerifyRequest(claim=CLAIM, video_published_date=bad)


# ------------------------------------------------------------------------------- caching


async def test_cache_separates_temporal_contexts_and_merges_equivalent_ones(
    settings: Settings,
) -> None:
    r = Retriever()
    llm = FakeLLM(
        json_responses={"ClaimAnalysisOutput": ANALYSIS_FACTUAL, "EvaluationOutput": EVAL}
    )
    svc = VerificationService(llm, settings, cache=TTLCache(3600, 10), retriever=r)
    for dates in (
        {"statement_date": "1998", "video_published_date": "2024-05-01"},  # compilation clip A
        {"statement_date": "2016", "video_published_date": "2024-05-01"},  # clip B, same video
        {"video_published_date": "2024-05-01"},  # date unknown
        # conflicting statement date is dropped: same context as "unknown", served from cache
        {"statement_date": "2025", "video_published_date": "2024-05-01"},
    ):
        await svc.verify(VerifyRequest(claim=CLAIM, **dates))
    assert r.calls == 3


# ---------------------------------------------------------------------------- extraction


def _extracted(**fields: Any) -> Any:
    out = ExtractionOutput(found=True, claim=CLAIM, claim_type="factual", **fields)
    usage = TokenUsage(prompt_tokens=1, output_tokens=1, total_tokens=2, search_queries=0)
    return build_extract_response(out, "dQw4w9WgXcQ", 10, TimeWindow(start_sec=0, end_sec=20),
                                  "m", 1, usage)  # fmt: skip


def test_extraction_reports_a_date_only_from_the_clip_and_only_if_valid() -> None:
    r = _extracted(statement_date="1998-05", statement_date_evidence="Caption: Senate, May 1998")
    assert (r.statement_date, r.statement_date_evidence) == ("1998-05", "Caption: Senate, May 1998")
    for bad in ("1998-13", "May 1998", "2999", ""):
        r = _extracted(statement_date=bad, statement_date_evidence="?")
        assert r.statement_date is None and r.statement_date_evidence is None


def test_extraction_prompt_forbids_using_the_upload_date() -> None:
    prompt = extraction_prompt(timestamp_sec=10, start_sec=0, end_sec=20, language="en")
    assert "do not use the upload date" in prompt
    assert "Archival footage, compilations and rebroadcasts" in prompt
    assert "Never invent" in EXTRACTION_SYSTEM


# ------------------------------------------------------------------ evidence publication


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ('<meta property="article:published_time" content="2024-08-30T14:02:00Z">', "2024-08-30"),
        ('<meta content="2024-08-30" name="pubdate">', "2024-08-30"),  # attributes reversed
        ('<script type="application/ld+json">{"datePublished": "2023-01-05"}</script>',
         "2023-01-05"),
        ('<time datetime="2022-11-02T09:00">Nov 2</time>', "2022-11-02"),
        ('<meta property="article:published_time" content="2099-01-01">', None),  # future
        ('<meta name="date" content="last Tuesday">', None),
        ("<p>No metadata.</p>", None),
    ],
)  # fmt: skip
def test_page_publication_date_from_metadata(html: str, expected: str | None) -> None:
    assert page_published_date(f"<html><head>{html}</head></html>", TODAY) == expected


async def test_fetched_page_carries_its_publication_date() -> None:
    page_html = (
        '<html><head><meta property="article:published_time" content="2024-08-30T10:00:00Z">'
        "</head><body><p>Text.</p></body></html>"
    )
    transport = httpx.MockTransport(lambda r: httpx.Response(200, html=page_html))
    async with httpx.AsyncClient(transport=transport) as http:
        page = await fetch_page("https://news.example.org/a", http, check_dns=False)
    assert page.published_date == "2024-08-30"


async def test_provider_dates_reach_sources_and_the_evaluator() -> None:
    def search_api(req: httpx.Request) -> httpx.Response:
        if req.url.host == "api.search.brave.com":
            return httpx.Response(200, json={"web": {"results": [
                {"url": "https://down.example/a", "title": "Jobs report",
                 "description": "Unemployment fell to a record low, the report said.",
                 "page_age": "2023-02-03T00:00:00"}]}})  # fmt: skip
        return httpx.Response(503)  # the page itself cannot be read: snippet fallback

    async with httpx.AsyncClient(transport=httpx.MockTransport(search_api)) as http:
        retriever = SearchRetriever(
            FailoverSearch([BraveProvider("k", http)]), http, check_dns=False
        )
        result = await retriever.retrieve(
            claim=CLAIM, queries=["unemployment record low"], scope_notes=None, language="en"
        )
    (src,) = result.sources
    assert src.origin == "search_snippet" and src.published_date == "2023-02-03"
    block = format_sources_for_prompt([src, EvidenceSource("S9", "https://b.example/", None, None)])
    assert "published: 2023-02-03" in block and "date unknown" in block


async def test_searxng_published_date_is_read() -> None:
    def api(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [
            {"url": "https://a.example/x", "title": "t", "content": "c",
             "publishedDate": "2021-07-01T12:00:00"}]})  # fmt: skip

    async with httpx.AsyncClient(transport=httpx.MockTransport(api)) as http:
        res = await SearxngProvider("https://searx.example", http).search(
            "q", language="en", max_results=5
        )
    assert res.hits[0].published_date == "2021-07-01"
