"""Reviewed reference records for real-world fact-check cases, and scoring against them.

A reference record is the *answer key* for one claim: its exact wording and speaker (with how
each was established), the claim split into individually testable propositions, the evidence
a reviewer relied on (with provenance), and the reference verdict.

Provenance matters because the application must never be credited with evidence it did not
retrieve itself: `user_supplied` and `reviewer_*` evidence is for scoring, never for display
as the system's own citations.

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


class Proposition(BaseModel):
    id: str
    text: str
    kind: Literal["attribution", "factual", "temporal", "characterization", "causal", "opinion"]
    reference_verdict: Verdict
    rationale: str


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
    propositions: list[Proposition]
    evidence: list[ReferenceEvidence]
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
