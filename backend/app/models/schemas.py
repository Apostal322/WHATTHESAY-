"""Public API request/response schemas (the contract with the extension)."""

from __future__ import annotations

import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ClaimType = Literal["factual", "prediction", "opinion", "ambiguous", "non_verifiable"]
Verdict = Literal["supported", "contradicted", "mixed", "insufficient_evidence", "not_verifiable"]
Stance = Literal["supports", "contradicts", "partially_supports", "unclear", "irrelevant"]
Language = Literal["auto", "en", "ru"]

MAX_TIMESTAMP_SEC = 12 * 60 * 60  # 12h: longer than any standard (non-live) upload we target


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def strip_invisible(text: str) -> str:
    """Remove control and invisible format characters (NUL, ESC, zero-width spaces, ...).

    They carry no meaning in a claim, can hide text from the user while the model still reads
    it, and break exact matching of quoted words. Ordinary whitespace is kept.
    """
    return "".join(
        ch for ch in text if ch in "\t\n\r" or unicodedata.category(ch) not in ("Cc", "Cf")
    )


# --------------------------------------------------------------------- usage


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    video_tokens: int | None = None
    audio_tokens: int | None = None
    search_queries: int = Field(default=0, description="Executed grounding searches (billed).")
    estimated_cost_usd: float | None = Field(
        default=None,
        description="Only set when prices are configured; an estimate, not a bill.",
    )


# ---------------------------------------------------------------- extraction


class ExtractRequest(_Strict):
    youtube_url: str | None = Field(default=None, max_length=300)
    video_id: str | None = Field(default=None, max_length=11)
    # strict: a number, not `true` or "12"
    timestamp_sec: float = Field(ge=0, le=MAX_TIMESTAMP_SEC, strict=True)
    duration_sec: float | None = Field(default=None, gt=0, le=MAX_TIMESTAMP_SEC, strict=True)
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
    timestamp_sec: float | None = Field(default=None, ge=0, le=MAX_TIMESTAMP_SEC, strict=True)
    language: Language = "auto"

    @field_validator("claim", "context", mode="before")
    @classmethod
    def _clean(cls, v: object) -> object:
        return strip_invisible(v) if isinstance(v, str) else v

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
        description="Evidence passages for this source; see excerpt_origin for what they are."
    )
    excerpt_origin: Literal["grounding_summary", "page_text", "search_snippet"] = Field(
        default="grounding_summary",
        description=(
            "grounding_summary: the research model's summary that Google Search grounding "
            "attributes to this source (not a quote). page_text: verbatim text from the page. "
            "search_snippet: the search engine's snippet; the page itself was not read."
        ),
    )
    stance: Stance | None = None
    assessment_note: str | None = None


class KeyFinding(BaseModel):
    text: str
    stance: Stance
    source_ids: list[str]


class VerdictAdjustment(BaseModel):
    """A deterministic safeguard that changed the model's verdict (not an advisory warning)."""

    rule: Literal[
        "verdict_without_backing_source",
        "attribution_without_direct_evidence",
        "exact_quote_not_confirmed",
    ]
    from_verdict: Verdict
    to_verdict: Verdict
    reason: str


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
    verdict_adjustments: list[VerdictAdjustment] = Field(
        default_factory=list,
        description="Safeguards that changed the verdict, in order. Empty when the model's "
        "verdict stood; guardrail_notes may still carry warnings.",
    )
    model: str
    models_used: dict[str, str] = Field(
        default_factory=dict,
        description="Model that actually served each step (analysis, retrieval, evaluation); "
        "differs from `model` when the fallback model was used.",
    )
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
    retrieval_backend: str = "gemini_grounding"
