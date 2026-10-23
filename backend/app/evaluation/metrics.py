"""Evaluation dataset schema and metric functions (pure, unit-tested).

See docs/evaluation.md for the protocol. These metrics are automatic proxies;
claim-match and evidence relevance should be spot-checked by a human.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Sequence
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.models.schemas import ClaimType, Verdict

CONFIDENT: set[Verdict] = {"supported", "contradicted"}


class EvidenceRef(BaseModel):
    url: str
    note: str | None = None


ReviewStatus = Literal["unreviewed", "human_verified", "disputed"]


class EvalItem(BaseModel):
    """One row of eval/*.jsonl.

    Labels count as ground truth only when `review_status` is `human_verified`, with the
    reviewer and date recorded. Everything else (unreviewed, disputed, AI-written examples) is
    reported separately as provisional and never as accuracy.
    """

    id: str
    video_id: str | None = Field(
        default=None, description="Omit for claim-only (verification) items."
    )
    timestamp_sec: float | None = None
    language: Literal["auto", "en", "ru"] = "auto"
    ground_truth_claim: str
    claim_type: ClaimType
    reference_verdict: Verdict
    supporting_evidence: list[EvidenceRef] = Field(default_factory=list)
    notes: str | None = None
    review_status: ReviewStatus = "unreviewed"
    reviewed_by: str | None = None
    reviewed_on: date | None = None

    @model_validator(mode="after")
    def _verified_needs_a_reviewer(self) -> EvalItem:
        if self.review_status == "human_verified" and not (self.reviewed_by and self.reviewed_on):
            raise ValueError("human_verified items need reviewed_by and reviewed_on")
        return self


class EvalRecord(BaseModel):
    """One system run on one item (written to eval/runs/)."""

    id: str
    extracted_claim: str | None = None
    extracted_claim_type: ClaimType | None = None
    extraction_status: str | None = None
    claim_f1: float | None = None
    predicted_verdict: Verdict | None = None
    source_urls: list[str] = Field(default_factory=list)
    relevant_source_count: int = 0
    valid_citation_count: int = 0
    reference_domain_recall: float | None = None
    extraction_latency_ms: int | None = None
    verification_latency_ms: int | None = None
    total_tokens: int = 0
    estimated_cost_usd: float | None = None
    error: str | None = None


_TOKEN_RE = re.compile(r"[\w%]+", re.UNICODE)


def tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def token_f1(prediction: str, reference: str) -> float:
    """SQuAD-style bag-of-tokens F1. A cheap proxy for 'same claim'."""
    pred, ref = tokens(prediction), tokens(reference)
    if not pred or not ref:
        return 0.0
    remaining = list(ref)
    common = 0
    for tok in pred:
        if tok in remaining:
            remaining.remove(tok)
            common += 1
    if common == 0:
        return 0.0
    precision, recall = common / len(pred), common / len(ref)
    return 2 * precision * recall / (precision + recall)


def is_false_confident(predicted: Verdict, reference: Verdict) -> bool:
    """A confident verdict that the reference does not license.

    Counts: supported/contradicted when the reference is anything else, and
    polarity flips (supported vs contradicted).
    """
    return predicted in CONFIDENT and predicted != reference


def domain(url: str) -> str:
    host = re.sub(r"^https?://", "", url.lower()).split("/", 1)[0]
    return host.removeprefix("www.")


def domain_recall(source_urls: Sequence[str], reference_urls: Sequence[str]) -> float | None:
    ref = {domain(u) for u in reference_urls}
    if not ref:
        return None
    got = {domain(u) for u in source_urls}
    return len(ref & got) / len(ref)


def _mean(values: Sequence[float]) -> float | None:
    return round(statistics.fmean(values), 4) if values else None


def _p(values: Sequence[int], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def _label_metrics(
    by_id: dict[str, EvalItem], records: Sequence[EvalRecord], threshold: float
) -> dict[str, object]:
    """Metrics that compare against labels; only as meaningful as the labels' review status."""
    extraction = [r for r in records if r.claim_f1 is not None]
    verified = [r for r in records if r.predicted_verdict is not None]
    type_correct = [
        r.extracted_claim_type == by_id[r.id].claim_type
        for r in extraction
        if r.extracted_claim_type is not None
    ]
    return {
        "extraction_n": len(extraction),
        "mean_claim_f1": _mean([r.claim_f1 for r in extraction if r.claim_f1 is not None]),
        f"claim_match_rate@f1>={threshold}": _mean(
            [float((r.claim_f1 or 0) >= threshold) for r in extraction]
        ),
        "claim_type_accuracy": _mean([float(x) for x in type_correct]),
        "verification_n": len(verified),
        "verdict_accuracy": _mean(
            [float(r.predicted_verdict == by_id[r.id].reference_verdict) for r in verified]
        ),
        "false_confident_rate": _mean(
            [
                float(is_false_confident(r.predicted_verdict, by_id[r.id].reference_verdict))  # type: ignore[arg-type]
                for r in verified
            ]
        ),
        "reference_domain_recall": _mean(
            [r.reference_domain_recall for r in verified if r.reference_domain_recall is not None]
        ),
    }


def summarize(
    items: Sequence[EvalItem], records: Sequence[EvalRecord], claim_match_threshold: float = 0.5
) -> dict[str, object]:
    """Label-based metrics only over human-verified items; the rest is reported as provisional.

    Latency, cost and citation checks do not depend on labels and use every record.
    """
    by_id = {i.id: i for i in items}
    human = {i.id for i in items if i.review_status == "human_verified"}
    summary = _summarize_all(items, records, claim_match_threshold)
    summary["human_verified"] = {
        "items": len(human),
        **_label_metrics(by_id, [r for r in records if r.id in human], claim_match_threshold),
    }
    summary["provisional_unreviewed_labels"] = {
        "items": len(items) - len(human),
        "note": "Agreement with labels nobody has verified. Not accuracy.",
        **_label_metrics(by_id, [r for r in records if r.id not in human], claim_match_threshold),
    }
    return summary


def _summarize_all(
    items: Sequence[EvalItem], records: Sequence[EvalRecord], claim_match_threshold: float = 0.5
) -> dict[str, object]:
    """Label-independent measurements over every record."""
    extraction = [r for r in records if r.claim_f1 is not None]
    verified = [r for r in records if r.predicted_verdict is not None]
    sources_total = sum(len(r.source_urls) for r in records)
    costs = [r.estimated_cost_usd for r in verified if r.estimated_cost_usd is not None]
    return {
        "items": len(items),
        "errors": sum(1 for r in records if r.error),
        "extraction": {
            "n": len(extraction),
            "latency_ms_p50": _p(
                [r.extraction_latency_ms for r in extraction if r.extraction_latency_ms], 0.5
            ),
            "latency_ms_p90": _p(
                [r.extraction_latency_ms for r in extraction if r.extraction_latency_ms], 0.9
            ),
        },
        "verification": {
            "n": len(verified),
            "citation_validity": round(
                sum(r.valid_citation_count for r in records) / sources_total, 4
            )
            if sources_total
            else None,
            "relevant_source_fraction": round(
                sum(r.relevant_source_count for r in records) / sources_total, 4
            )
            if sources_total
            else None,
            "latency_ms_p50": _p(
                [r.verification_latency_ms for r in verified if r.verification_latency_ms], 0.5
            ),
            "latency_ms_p90": _p(
                [r.verification_latency_ms for r in verified if r.verification_latency_ms], 0.9
            ),
            "mean_cost_usd": _mean(costs),
            "mean_total_tokens": _mean([float(r.total_tokens) for r in verified]),
        },
    }
