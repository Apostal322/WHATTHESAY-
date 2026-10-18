"""Pure helpers for live integration validation (scripts/live_validation.py).

Kept free of network I/O so they can be unit-tested.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from app.evaluation.metrics import tokens

# Gemini 3 media resolution docs: low and medium video frames cost 70 tokens each,
# high costs 280. The audio rate is an assumption carried over from earlier published
# figures; the clipping verdict below does not depend on it.
VIDEO_TOKENS_PER_FRAME = {"low": 70, "medium": 70, "high": 280}
AUDIO_TOKENS_PER_SEC_ASSUMED = 32

PLACEHOLDER = "REPLACE"


class LiveCase(BaseModel):
    """A human-verified moment in a public YouTube video."""

    id: str
    youtube_url: str
    timestamp_sec: float = Field(ge=0)
    language: Literal["auto", "en", "ru"] = "auto"
    duration_sec: float | None = Field(default=None, gt=0, description="Full video length.")
    ground_truth_claim: str | None = Field(
        default=None,
        description="What a human heard just before timestamp_sec. None = exploratory case: "
        "no accuracy is computed and the extracted claim is verified.",
    )
    verified_by: str | None = Field(
        default=None, description="Who checked the statement against the video, and when."
    )
    reference_verdict: str | None = None

    @property
    def exploratory(self) -> bool:
        return not self.ground_truth_claim

    def is_placeholder(self) -> bool:
        return PLACEHOLDER in self.model_dump_json()


@dataclass(frozen=True)
class ClipRun:
    window_sec: int
    prompt_tokens: int
    video_tokens: int | None
    audio_tokens: int | None


ClipVerdict = Literal["clipping_honoured", "clipping_ignored", "inconclusive"]


def expected_clip_tokens(window_sec: float, fps: float, resolution: str) -> int:
    frames = max(1, round(window_sec * fps))
    return frames * VIDEO_TOKENS_PER_FRAME[resolution] + round(
        window_sec * AUDIO_TOKENS_PER_SEC_ASSUMED
    )


def media_tokens(run: ClipRun) -> int:
    """Video+audio tokens when reported per modality, else total prompt tokens."""
    if run.video_tokens is not None or run.audio_tokens is not None:
        return (run.video_tokens or 0) + (run.audio_tokens or 0)
    return run.prompt_tokens


def clipping_verdict(short: ClipRun, long: ClipRun) -> tuple[ClipVerdict, str]:
    """Decide from two windows of the same video whether offsets were honoured.

    If offsets are honoured, media tokens scale with the window length. If
    they are ignored, both requests process the same full video, so counts
    are about equal.
    """
    window_ratio = long.window_sec / short.window_sec
    a, b = media_tokens(short), media_tokens(long)
    token_ratio = b / max(1, a)
    detail = f"window ratio {window_ratio:.2f}, media-token ratio {token_ratio:.2f} ({a} -> {b})"
    if token_ratio >= 1 + 0.6 * (window_ratio - 1):
        return "clipping_honoured", detail
    if token_ratio <= 1.1:
        return "clipping_ignored", detail
    return "inconclusive", detail


_TAG_RE = re.compile(r"<[^>]+>")
_DROP_RE = re.compile(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", re.I | re.S)
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def html_to_text(raw: str) -> str:
    text = _DROP_RE.sub(" ", raw)
    text = _TAG_RE.sub(" ", text)
    return " ".join(html.unescape(text).split())


def _norm_num(n: str) -> str:
    return n.replace(",", ".")


def lexical_support(excerpt: str, page_text: str) -> dict[str, float | int]:
    """Rough check that a grounded excerpt is reflected in the page text.

    This is not proof of support, only a screen for excerpts whose numbers or
    content words do not appear on the cited page, which then need manual review.
    """
    page_tokens = set(tokens(page_text))
    page_nums = {_norm_num(n) for n in _NUM_RE.findall(page_text)}
    content = [t for t in tokens(excerpt) if len(t) >= 5 and not t.isdigit()]
    nums = [_norm_num(n) for n in _NUM_RE.findall(excerpt)]
    covered = sum(1 for t in content if t in page_tokens)
    nums_found = sum(1 for n in nums if n in page_nums)
    return {
        "content_terms": len(content),
        "content_coverage": round(covered / len(content), 3) if content else 1.0,
        "numbers": len(nums),
        "numbers_found": nums_found,
    }


def needs_review(check: dict[str, float | int]) -> bool:
    return float(check["content_coverage"]) < 0.6 or check["numbers_found"] < check["numbers"]
