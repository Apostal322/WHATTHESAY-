"""Human annotation of real video moments, kept apart from anything a model proposed.

`eval/annotation/cases.jsonl` holds one case per moment: a `machine` block (what a model
extracted, with its run artifact) and a `human` block that only a person fills in. A case
becomes ground truth (an `EvalItem`) only when its human block is complete and marked
`human_verified` with a reviewer and date. Machine proposals are never copied into labels.
"""

from __future__ import annotations

from datetime import date
from typing import ClassVar, Literal

from pydantic import BaseModel, Field, model_validator

from app.evaluation.metrics import EvalItem, EvidenceRef, ReviewStatus
from app.models.schemas import ClaimType, Verdict
from app.services.youtube import parse_video_id


class MachineProposal(BaseModel):
    """Model output for this moment. Evidence of what the system did, not of what was said."""

    source: Literal["live_extraction", "none"] = "none"
    run_artifact: str | None = None
    model: str | None = None
    claim: str | None = None
    claim_type: ClaimType | None = None
    words_heard: str | None = Field(default=None, description="Model transcription; unverified.")
    exact_quote_model_reported: bool | None = None


class HumanLabel(BaseModel):
    """Filled in by a person who watched the moment. Every field starts empty."""

    exact_words: str | None = Field(default=None, description="What was said, transcribed by hand.")
    speaker: str | None = None
    claim: str | None = Field(default=None, description="The checkable claim, in the user's terms.")
    claim_type: ClaimType | None = None
    reference_verdict: Verdict | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)
    status: ReviewStatus = "unreviewed"
    reviewer: str | None = None
    reviewed_on: date | None = None
    disagreement_notes: str | None = None

    REQUIRED: ClassVar[tuple[str, ...]] = (
        "exact_words",
        "speaker",
        "claim",
        "claim_type",
        "reference_verdict",
    )

    def missing(self) -> list[str]:
        gaps = [f for f in self.REQUIRED if getattr(self, f) in (None, "")]
        if self.reference_verdict in ("supported", "contradicted", "mixed") and not self.evidence:
            gaps.append("evidence")
        if self.status == "human_verified":
            gaps += [f for f in ("reviewer", "reviewed_on") if getattr(self, f) is None]
        return gaps

    @model_validator(mode="after")
    def _verified_means_complete(self) -> HumanLabel:
        if self.status == "human_verified" and self.missing():
            raise ValueError(f"human_verified but missing: {', '.join(self.missing())}")
        return self


class AnnotationCase(BaseModel):
    id: str
    youtube_url: str
    timestamp_sec: float = Field(ge=0)
    language: Literal["en", "ru", "auto"]
    review_hints: list[str] = Field(
        default_factory=list, description="Questions for the reviewer; not labels."
    )
    machine: MachineProposal = Field(default_factory=MachineProposal)
    human: HumanLabel = Field(default_factory=HumanLabel)

    def to_eval_item(self) -> EvalItem | None:
        """The ground-truth item, or None until a person has verified the case."""
        h = self.human
        if h.status != "human_verified":
            return None
        assert h.claim and h.claim_type and h.reference_verdict  # guaranteed by the validator
        return EvalItem(
            id=self.id,
            video_id=parse_video_id(self.youtube_url, None),
            timestamp_sec=self.timestamp_sec,
            language=self.language,
            ground_truth_claim=h.claim,
            claim_type=h.claim_type,
            reference_verdict=h.reference_verdict,
            supporting_evidence=h.evidence,
            notes=h.disagreement_notes,
            review_status="human_verified",
            reviewed_by=h.reviewer,
            reviewed_on=h.reviewed_on,
        )
