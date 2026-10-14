"""Offline evidence fixtures: source excerpts captured once, replayed in regression tests.

A fixture is NOT live retrieval. It stores, per source, the original URL and metadata and at
most a few short verbatim excerpts that the application's own fetcher cut from the page
(`capture_from_paragraphs`), with the time, method and a hash of the page text they came
from. Full pages are not stored.

`FixtureRetriever` replays the captured excerpts through the normal verification pipeline
(analysis -> evaluation -> guardrails), so the business logic under test is the real one;
only the network step is replaced. It refuses to run without captured excerpts, and every
response it feeds carries a notice saying the evidence is an offline fixture.

Sources that could not be captured stay in the fixture as `pending_capture` with a log of
the attempts. Their text is never filled in by hand or from memory.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.errors import RetrievalUnavailableError
from app.services.citations import EvidenceSource, is_safe_display_url, normalize_domain
from app.services.evidence import MAX_PASSAGE_CHARS, excerpt_is_verbatim, passage_windows
from app.services.retrieval import RetrievalResult

MAX_EXCERPTS_PER_SOURCE = 3
FIXTURE_NOTICE = (
    "Evidence came from an offline regression fixture (excerpts captured on {dates}), not from "
    "a live search. It shows how the system reasons over these sources, not that it can find them."
)

CaptureMethod = Literal[
    "app_fetch_page",  # scripts/capture_evidence.py fetched the URL with app.services.fetch
    "user_saved_html",  # a person saved the page in a browser; the app parsed that file
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Excerpt(_Strict):
    text: str = Field(max_length=MAX_PASSAGE_CHARS)
    anchor: str = Field(description="The anchor phrase that selected this passage.")


class Capture(_Strict):
    captured_at: str
    method: CaptureMethod
    environment: str = Field(description="Where it ran, e.g. 'local Windows 11'. No hostnames.")
    final_url: str
    page_sha256: str = Field(description="SHA-256 of the extracted page text (paragraphs).")
    paragraph_count: int
    excerpts: list[Excerpt] = Field(max_length=MAX_EXCERPTS_PER_SOURCE)
    missing_anchors: list[str] = Field(default_factory=list)


class CaptureAttempt(_Strict):
    at: str
    environment: str
    result: str = Field(description="Short machine code, e.g. 'dns_blocked', 'http_403'.")


class FixtureSource(_Strict):
    id: str = Field(description="Same id as in the reference record's evidence list.")
    url: str
    publisher: str
    source_type: Literal[
        "primary_transcript", "official_record", "wire_report", "news_report", "other"
    ]
    published: str | None = None
    located_by: Literal["user", "ai_assistant_search", "application"]
    relevant_to: list[str] = Field(
        description="Propositions this source is expected to bear on. A reason to include it, "
        "not a stance."
    )
    anchors: list[str] = Field(
        min_length=1, description="Phrases that locate the relevant passages on the page."
    )
    capture: Capture | None = None
    attempts: list[CaptureAttempt] = Field(default_factory=list)

    @property
    def status(self) -> Literal["captured", "pending_capture"]:
        return "captured" if self.capture and self.capture.excerpts else "pending_capture"

    @model_validator(mode="after")
    def _check(self) -> FixtureSource:
        if not is_safe_display_url(self.url):
            raise ValueError(f"{self.id}: unsafe url")
        if self.capture and not is_safe_display_url(self.capture.final_url):
            raise ValueError(f"{self.id}: unsafe final_url")
        return self


class EvidenceFixture(_Strict):
    kind: Literal["offline_regression_fixture"]
    live_retrieval: Literal[False] = Field(
        description="Always false: a fixture never represents a live search."
    )
    case_id: str
    description: str
    excerpt_policy: str
    sources: list[FixtureSource]

    def captured(self) -> list[FixtureSource]:
        return [s for s in self.sources if s.status == "captured"]

    def source(self, sid: str) -> FixtureSource:
        return next(s for s in self.sources if s.id == sid)


# ------------------------------------------------------------------- capture


def page_hash(paragraphs: list[str]) -> str:
    return hashlib.sha256("\n".join(paragraphs).encode("utf-8")).hexdigest()


def _fold(text: str) -> str:
    return " ".join(text.lower().split())


def capture_from_paragraphs(
    paragraphs: list[str],
    anchors: list[str],
    *,
    final_url: str,
    method: CaptureMethod,
    environment: str,
    now: datetime | None = None,
) -> Capture:
    """Cut at most three short verbatim passages that contain the anchor phrases.

    Uses the same sentence windows as live retrieval (`passage_windows`), so an excerpt is
    always a verbatim substring of one page paragraph. Anchors not found are reported, never
    substituted.
    """
    excerpts: list[Excerpt] = []
    missing: list[str] = []
    for anchor in anchors:
        needle = _fold(anchor)
        hit = next(
            (w for p in paragraphs for w in passage_windows(p) if needle in _fold(w)),
            None,
        )
        if hit is None:
            missing.append(anchor)
        elif (
            all(e.text != hit for e in excerpts)
            and len(excerpts) < MAX_EXCERPTS_PER_SOURCE
            and excerpt_is_verbatim(hit, paragraphs)
        ):
            excerpts.append(Excerpt(text=hit, anchor=anchor))
    return Capture(
        captured_at=(now or datetime.now(UTC)).isoformat(timespec="seconds"),
        method=method,
        environment=environment,
        final_url=final_url,
        page_sha256=page_hash(paragraphs),
        paragraph_count=len(paragraphs),
        excerpts=excerpts,
        missing_anchors=missing,
    )


# ------------------------------------------------------------------ retrieval

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


class FixtureRetriever:
    """Replays captured excerpts as `page_text` evidence.

    It ignores the queries: nothing is searched.
    """

    name = "offline_fixture"

    def __init__(self, fixture: EvidenceFixture, source_ids: list[str] | None = None) -> None:
        self._fixture = fixture
        self._ids = source_ids

    async def retrieve(
        self, *, claim: str, queries: list[str], scope_notes: str | None, language: str
    ) -> RetrievalResult:
        chosen = [s for s in self._fixture.captured() if self._ids is None or s.id in self._ids]
        pending = [s.id for s in self._fixture.sources if s.status == "pending_capture"]
        if not chosen:
            raise RetrievalUnavailableError(
                "The offline fixture has no captured excerpts for these sources. Run "
                "scripts/capture_evidence.py on a machine with web access first.",
                diagnostics={"backend": self.name, "pending_capture": pending},
            )
        sources = [
            EvidenceSource(
                id=f"S{i}",
                url=s.capture.final_url if s.capture else s.url,
                title=f"{s.publisher} ({s.source_type.replace('_', ' ')})",
                domain=normalize_domain(s.url),
                excerpts=[e.text for e in s.capture.excerpts] if s.capture else [],
                origin="page_text",
            )
            for i, s in enumerate(chosen, start=1)
        ]
        stamps = [s.capture.captured_at for s in chosen if s.capture]
        dates = sorted({m.group(0) for t in stamps if (m := _DATE_RE.match(t))})
        return RetrievalResult(
            sources=sources,
            executed_queries=[],  # nothing was searched
            backend=self.name,
            empty_reason="The offline fixture has no captured excerpts.",
            notices=[FIXTURE_NOTICE.format(dates=", ".join(dates))],
            diagnostics={
                "case_id": self._fixture.case_id,
                "fixture_sources": {f"S{i}": s.id for i, s in enumerate(chosen, start=1)},
                "pending_capture": pending,
            },
        )
