"""Public API request/response schemas (the contract with the extension)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ClaimType = Literal["factual", "prediction", "opinion", "ambiguous", "non_verifiable"]
Verdict = Literal["supported", "contradicted", "mixed", "insufficient_evidence", "not_verifiable"]
Stance = Literal["supports", "contradicts", "partially_supports", "irrelevant"]
Language = Literal["auto", "en", "ru"]

MAX_TIMESTAMP_SEC = 12 * 60 * 60  # 12h: longer than any standard (non-live) upload we target


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --------------------------------------------------------------------- usage


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    video_tokens: int | None = None
    audio_tokens: int | None = None
    estimated_cost_usd: float | None = Field(
        default=None,
        description="Only set when prices are configured; an estimate, not a bill.",
    )


# ---------------------------------------------------------------- extraction


class ExtractRequest(_Strict):
    youtube_url: str | None = Field(default=None, max_length=300)
    video_id: str | None = Field(default=None, max_length=11)
    timestamp_sec: float = Field(ge=0, le=MAX_TIMESTAMP_SEC)
    duration_sec: float | None = Field(default=None, gt=0, le=MAX_TIMESTAMP_SEC)
    language: Language = "auto"

    @model_validator(mode="after")
    def _one_video_reference(self) -> ExtractRequest:
        if not self.youtube_url and not self.video_id:
            raise ValueError("Provide youtube_url or video_id.")
        if self.duration_sec is not None and self.timestamp_sec > self.duration_sec + 1:
            raise ValueError("timestamp_sec is beyond the video duration.")
        return self


class TimeWindow(BaseModel):
    start_sec: int
    end_sec: int


class ExtractResponse(BaseModel):
    status: Literal["success", "no_claim_found"]
    video_id: str
    timestamp_sec: int
    window: TimeWindow
    claim: str | None
    claim_type: ClaimType | None
    context: str | None
    evidence_from_video: str | None = Field(
        description="What the speaker said: a transcription if is_exact_quote, else a paraphrase."
    )
    is_exact_quote: bool = Field(
        description=(
            "Model-reported. True only when the model asserts a verbatim transcription. "
            "Not independently verified against a transcript."
        )
    )
    requires_user_confirmation: Literal[True] = True
    language: str | None
    reason: str | None = Field(default=None, description="Why no claim was found, if applicable.")
    model: str
    latency_ms: int
    cached: bool = False
    usage: TokenUsage


# -------------------------------------------------------------- verification


class VerifyRequest(_Strict):
    claim: str = Field(min_length=5, max_length=1000)
    context: str | None = Field(default=None, max_length=2000)
    claim_type_hint: ClaimType | None = None
    video_id: str | None = Field(default=None, max_length=11)
    timestamp_sec: float | None = Field(default=None, ge=0, le=MAX_TIMESTAMP_SEC)
    language: Language = "auto"

    @field_validator("claim")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not any(ch.isalnum() for ch in v):
            raise ValueError("Claim must contain text.")
        return v


class SourceRef(BaseModel):
    id: str = Field(description="Stable within one response, e.g. 'S1'.")
    title: str | None
    url: str
    domain: str | None
    publisher: str | None = Field(default=None, description="Only set when known; never guessed.")
    published_date: str | None = Field(default=None, description="Only set when known.")
    retrieved_at: str
    excerpts: list[str] = Field(
        description=(
            "Passages of the research model's grounded summary that Google Search grounding "
            "attributes to this source. Summaries, not verbatim quotes from the page."
        )
    )
    stance: Stance | None = None
    assessment_note: str | None = None


class KeyFinding(BaseModel):
    text: str
    stance: Stance
    source_ids: list[str]


class VerifyResponse(BaseModel):
    claim: str
    normalized_claim: str
    claim_type: ClaimType
    verdict: Verdict
    explanation: str
    key_findings: list[KeyFinding]
    sources: list[SourceRef]
    limitations: list[str]
    search_queries: list[str]
    independent_source_count: int = Field(
        description="Distinct domains among sources that support or contradict the claim."
    )
    guardrail_notes: list[str] = Field(
        description="Deterministic post-checks that changed or qualified the model's output."
    )
    model: str
    latency_ms: int
    cached: bool = False
    usage: TokenUsage


# -------------------------------------------------------------------- errors


class ErrorBody(BaseModel):
    code: str
    message: str
    retryable: bool = False


class ErrorResponse(BaseModel):
    error: ErrorBody


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    gemini_configured: bool
    extraction_model: str
    verification_model: str
