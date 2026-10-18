"""Reviewed reference records for real-world fact-check cases, and scoring against them.

A reference record is the *answer key* for one claim: its exact wording and speaker (with how
each was established), the claim split into individually testable propositions, the evidence
a reviewer relied on (with provenance), and the reference verdict.

Provenance matters because the application must never be credited with evidence it did not
retrieve itself: `user_supplied` and `reviewer_*` evidence is for scoring, never for display
as the system's own citations.

Four states of a source are kept apart, because they are easy to conflate:

- *externally located*: someone found the URL (`located_by`); nobody has necessarily read it;
- *inspected by the application*: the app's own fetcher opened it and kept verbatim excerpts
  (`inspected_by_app`, pointing at an offline evidence-fixture capture);
- *human-approved*: a named person checked the source against the proposition
  (`human_approved`); only possible in a human-reviewed record;
- *AI interpretation*: a stance an AI assistant inferred (`stance_basis` says from what).

Scores are only *counted* when the record is human-verified; otherwise they are reported as
provisional, so an AI-drafted answer key can never produce an accuracy number.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.schemas import Stance, Verdict

Provenance = Literal[
    "user_supplied",  # a person handed us the URL; nobody on our side has read it yet
    "reviewer_fetched_page",  # the reviewer read the page itself
    "reviewer_search_summary",  # the reviewer saw only a search engine's summary of it
    "app_retrieved",  # the application found it on its own (live run)
]
ConfirmationStatus = Literal[
    "verified_verbatim",  # checked word for word against the video or an authentic transcript
    "corroborated_secondary",  # consistent across independent secondary reports
    "model_reported",  # only the extraction model says so
    "unverified",
]
ReviewStatus = Literal["draft", "ai_reviewed", "human_verified"]
SourceType = Literal[
    "primary_transcript",  # a transcript of the event itself (debate, interview)
    "official_record",  # a government record (e.g. the state's ballot-initiative page)
    "wire_report",
    "news_report",
    "other",
]
StanceBasis = Literal[
    "none",  # no stance recorded
    "ai_interpretation_of_search_summary",  # an AI read a search engine's summary, not the page
    "ai_interpretation_of_page_excerpt",  # an AI read verbatim excerpts the app captured
    "human_assessment",  # a person read the source and recorded the stance
]


class ReferenceEvidence(BaseModel):
    id: str
    url: str
    publisher: str
    published: str | None = None
    provenance: Provenance
    opened_by_reviewer: bool = Field(
        description="True only if the reviewer read the page itself, not a summary of it."
    )
    stances: dict[str, Stance] = Field(
        default_factory=dict, description="Proposition id -> stance of this source."
    )
    note: str
    source_type: SourceType = "news_report"
    located_by: Literal["user", "ai_assistant_search", "application"] = Field(
        default="user", description="Who found the URL. Finding a URL is not reading it."
    )
    located_on: str | None = None
    user_statement: str | None = Field(
        default=None,
        description="What the person who located the source said it contains (their words, "
        "not checked by anyone else).",
    )
    inspected_by_app: bool = Field(
        default=False,
        description="True only if the application's own fetcher opened the page and kept "
        "verbatim excerpts; `app_capture` then names the fixture capture.",
    )
    app_capture: str | None = None
    human_approved: bool = Field(
        default=False, description="A named person checked this source against the propositions."
    )
    stance_basis: StanceBasis = "none"

    def model_post_init(self, __context: Any) -> None:
        if self.stances and self.stance_basis == "none":
            raise ValueError(f"{self.id}: stances need a stance_basis")
        if self.stance_basis == "human_assessment" and not self.human_approved:
            raise ValueError(f"{self.id}: a human assessment must be human_approved")
        if self.inspected_by_app != bool(self.app_capture):
            raise ValueError(f"{self.id}: inspected_by_app requires (and implies) app_capture")
        if self.stance_basis == "ai_interpretation_of_page_excerpt" and not self.inspected_by_app:
            raise ValueError(f"{self.id}: no captured excerpt to interpret")


class Event(BaseModel):
    """A dated statement the claim refers to, for chronology checks."""

    id: str
    description: str
    date: str = Field(description="ISO date (YYYY-MM-DD).")
    date_basis: Literal["confirmed_primary", "reported", "unconfirmed"] = Field(
        description="confirmed_primary: checked against the primary record; reported: the "
        "date given by secondary reports (e.g. publication date, which may differ from when "
        "an interview was recorded)."
    )
    evidence_ids: list[str] = Field(default_factory=list)
    note: str | None = None


class TemporalRelation(BaseModel):
    from_event: str
    to_event: str
    relation: Literal["same_day", "next_day", "before", "after"]


class Proposition(BaseModel):
    id: str
    text: str
    kind: Literal[
        "attribution",
        "factual",
        "temporal",
        "characterization",
        "legal_effect",
        "causal",
        "opinion",
    ]
    reference_verdict: Verdict
    acceptable_verdicts: list[Verdict] = Field(
        default_factory=list, description="Defaults to [reference_verdict]."
    )
    rationale: str
    temporal: TemporalRelation | None = None
    primary_evidence: list[str] = Field(
        default_factory=list,
        description="Evidence ids that would settle this proposition (whether or not opened).",
    )

    def model_post_init(self, __context: Any) -> None:
        if not self.acceptable_verdicts:
            self.acceptable_verdicts = [self.reference_verdict]
        if self.reference_verdict not in self.acceptable_verdicts:
            raise ValueError(f"{self.id}: acceptable_verdicts must include reference_verdict")
        if (self.kind == "temporal") != (self.temporal is not None):
            raise ValueError(f"{self.id}: temporal propositions (only) need a temporal relation")


class Review(BaseModel):
    reviewer: str
    reviewer_kind: Literal["human", "ai_assistant"]
    status: ReviewStatus
    date: str
    method: str
    open_items: list[str] = Field(default_factory=list)


class ReferenceRecord(BaseModel):
    id: str
    video_url: str
    video_timestamp_sec: float
    transcript_timestamp_note: str
    speaker: str
    speaker_status: ConfirmationStatus
    quote: str
    quote_status: ConfirmationStatus
    claim: str
    context: str
    claim_date: str | None = Field(
        default=None, description="ISO date the statement was made; judge it as of this date."
    )
    propositions: list[Proposition]
    evidence: list[ReferenceEvidence]
    events: list[Event] = Field(default_factory=list)
    reference_verdict: Verdict
    acceptable_verdicts: list[Verdict] = Field(
        description="Verdicts a careful fact-checker could defend; must include reference_verdict."
    )
    verdict_rationale: str
    review: Review

    def model_post_init(self, __context: Any) -> None:
        if self.reference_verdict not in self.acceptable_verdicts:
            raise ValueError("acceptable_verdicts must include reference_verdict")
        ids = {p.id for p in self.propositions}
        for ev in self.evidence:
            unknown = set(ev.stances) - ids
            if unknown:
                raise ValueError(f"{ev.id}: stances for unknown propositions {sorted(unknown)}")
        r = self.review
        if r.status == "human_verified" and r.reviewer_kind != "human":
            raise ValueError("only a human reviewer can mark a record human_verified")
        if r.reviewer_kind != "human" and any(e.human_approved for e in self.evidence):
            raise ValueError("human_approved evidence requires a human reviewer")
        evidence_ids = {e.id for e in self.evidence}
        event_ids = {e.id for e in self.events}
        for event in self.events:
            if missing := set(event.evidence_ids) - evidence_ids:
                raise ValueError(f"{event.id}: unknown evidence {sorted(missing)}")
        for p in self.propositions:
            if missing := set(p.primary_evidence) - evidence_ids:
                raise ValueError(f"{p.id}: unknown evidence {sorted(missing)}")
            if p.temporal and {p.temporal.from_event, p.temporal.to_event} - event_ids:
                raise ValueError(f"{p.id}: temporal relation names an unknown event")

    def proposition(self, pid: str) -> Proposition:
        return next(p for p in self.propositions if p.id == pid)


CONFIDENT: set[Verdict] = {"supported", "contradicted"}


def score_verdict(system_verdict: Verdict | None, ref: ReferenceRecord) -> dict[str, Any]:
    """Compare a system verdict with the reference. Provisional unless human-verified.

    A *false confident verdict* is a confident system verdict (supported/contradicted) that
    the reference does not accept: the primary safety metric in docs/evaluation.md.
    """
    counted = ref.review.status == "human_verified"
    if system_verdict is None:
        return {"counted": counted, "verdict": None, "outcome": "no_verdict"}
    acceptable = system_verdict in ref.acceptable_verdicts
    return {
        "counted": counted,
        "verdict": system_verdict,
        "matches_reference": system_verdict == ref.reference_verdict,
        "acceptable": acceptable,
        "false_confident": system_verdict in CONFIDENT and not acceptable,
        "outcome": "acceptable" if acceptable else "unacceptable",
    }


def score_proposition(
    system_verdict: Verdict | None, prop: Proposition, ref: ReferenceRecord
) -> dict[str, Any]:
    """Like `score_verdict`, for one proposition; provisional unless human-verified."""
    counted = ref.review.status == "human_verified"
    if system_verdict is None:
        return {"counted": counted, "verdict": None, "outcome": "no_verdict"}
    acceptable = system_verdict in prop.acceptable_verdicts
    return {
        "counted": counted,
        "verdict": system_verdict,
        "matches_reference": system_verdict == prop.reference_verdict,
        "acceptable": acceptable,
        "false_confident": system_verdict in CONFIDENT and not acceptable,
        "outcome": "acceptable" if acceptable else "unacceptable",
    }
