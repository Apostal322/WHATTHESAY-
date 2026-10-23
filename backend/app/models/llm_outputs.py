"""Schemas requested from the model via structured output.

These are intentionally flat and separate from the public API schemas: model
output is untrusted, gets validated against these, and is then post-processed
by deterministic code before anything reaches the user.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.models.schemas import ClaimType, Stance, Verdict


class ExtractionOutput(BaseModel):
    found: bool = Field(description="False if no checkable statement is spoken near the timestamp.")
    claim: str | None = Field(
        default=None,
        description="Self-contained statement in the spoken language, pronouns resolved.",
    )
    claim_type: ClaimType | None = None
    evidence_from_video: str | None = Field(
        default=None, description="What was said: exact words if confident, else a paraphrase."
    )
    is_exact_quote: bool = Field(
        default=False,
        description="True ONLY if evidence_from_video reproduces the spoken words exactly.",
    )
    context: str | None = Field(default=None, description="1-2 sentences of surrounding context.")
    language: str | None = Field(default=None, description="ISO 639-1 code of the speech.")
    statement_date: str | None = Field(
        default=None,
        description="When the statement was said or recorded, ONLY if the clip itself shows or "
        "states it: YYYY, YYYY-MM or YYYY-MM-DD. Null otherwise.",
    )
    statement_date_evidence: str | None = Field(
        default=None, description="What in the clip shows that date."
    )
    reason_if_not_found: str | None = None


class ClaimAnalysisOutput(BaseModel):
    normalized_claim: str = Field(description="Precise, self-contained restatement of the claim.")
    claim_type: ClaimType
    is_checkworthy: bool = Field(description="True if it contains an objectively checkable fact.")
    reason: str = Field(description="One sentence explaining the classification.")
    search_queries: list[str] = Field(description="2-4 focused web search queries.")
    scope_notes: str | None = Field(
        default=None,
        description="Time period, geography, definitions or numbers that the check depends on.",
    )
    attributed_to: str = Field(
        default="",
        description="If the claim is about what a specific person or organization said or "
        "wrote (not about the facts themselves), who; otherwise empty.",
    )
    exact_words: list[str] = Field(
        default_factory=list,
        description="Words the claim presents as that speaker's exact wording; empty if the "
        "claim paraphrases.",
    )


class SourceAssessment(BaseModel):
    source_id: str
    stance: Stance
    note: str = Field(description="Why, in one sentence, mentioning scope/date issues if any.")


class FindingOutput(BaseModel):
    text: str
    stance: Stance
    source_ids: list[str]


class EvaluationOutput(BaseModel):
    source_assessments: list[SourceAssessment]
    verdict: Verdict
    explanation: str = Field(description="2-4 sentences; cite sources inline as [S1].")
    key_findings: list[FindingOutput]
    limitations: list[str]


class PassageSpeaker(BaseModel):
    passage_id: str
    speaker_is_named: bool = Field(
        description="True only if the passage shows these are the words of the person the claim "
        "names (names may be transliterated or translated)."
    )


class PartRelation(BaseModel):
    passage_id: str
    relation: Literal["entails", "contradicts", "neutral"]


class ClaimPart(BaseModel):
    text: str = Field(description="One assertion about what the person said.")
    relations: list[PartRelation] = Field(description="One per passage.")


class EntailmentOutput(BaseModel):
    speakers: list[PassageSpeaker] = Field(description="One per passage.")
    parts: list[ClaimPart] = Field(description="Every assertion about what was said.")
