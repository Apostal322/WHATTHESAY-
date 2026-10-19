import json
from pathlib import Path

import pytest

from app.evaluation.metrics import (
    EvalItem,
    EvalRecord,
    domain_recall,
    is_false_confident,
    summarize,
    token_f1,
)

REPO = Path(__file__).resolve().parents[2]


def test_token_f1() -> None:
    assert token_f1("AI replaced 80% of engineers", "AI replaced 80% of engineers") == 1.0
    assert token_f1("", "x") == 0.0
    assert token_f1("cats", "dogs") == 0.0
    assert (
        0
        < token_f1("AI replaced 80% of software engineers", "AI has replaced 80% of engineers")
        < 1
    )
    assert token_f1("ИИ заменил 80% программистов", "ИИ заменил 80% программистов") == 1.0


@pytest.mark.parametrize(
    ("pred", "ref", "expected"),
    [
        ("supported", "supported", False),
        ("supported", "contradicted", True),
        ("contradicted", "insufficient_evidence", True),
        ("supported", "mixed", True),
        ("insufficient_evidence", "supported", False),  # cautious, not falsely confident
        ("not_verifiable", "contradicted", False),
    ],
)
def test_false_confident(pred: str, ref: str, expected: bool) -> None:
    assert is_false_confident(pred, ref) is expected  # type: ignore[arg-type]


def test_domain_recall() -> None:
    assert (
        domain_recall(["https://www.bls.gov/x"], ["https://bls.gov/y", "https://oecd.org"]) == 0.5
    )
    assert domain_recall(["https://a.org"], []) is None


def test_summarize() -> None:
    items = [
        EvalItem(
            id="1",
            ground_truth_claim="a b c",
            claim_type="factual",
            reference_verdict="contradicted",
            review_status="human_verified",
            reviewed_by="reviewer-a",
            reviewed_on="2026-10-19",
        ),
        EvalItem(
            id="2",
            ground_truth_claim="d e f",
            claim_type="opinion",
            reference_verdict="not_verifiable",
            review_status="human_verified",
            reviewed_by="reviewer-a",
            reviewed_on="2026-10-19",
        ),
    ]
    records = [
        EvalRecord(
            id="1",
            claim_f1=1.0,
            extracted_claim_type="factual",
            predicted_verdict="contradicted",
            source_urls=["https://a.org", "https://b.org"],
            valid_citation_count=2,
            relevant_source_count=1,
            verification_latency_ms=1000,
        ),
        EvalRecord(
            id="2",
            claim_f1=0.2,
            extracted_claim_type="factual",
            predicted_verdict="supported",
            verification_latency_ms=3000,
        ),
    ]
    s = summarize(items, records)
    h = s["human_verified"]
    assert h["items"] == 2  # type: ignore[index]
    assert h["claim_match_rate@f1>=0.5"] == 0.5  # type: ignore[index]
    assert h["claim_type_accuracy"] == 0.5  # type: ignore[index]
    assert h["verdict_accuracy"] == 0.5  # type: ignore[index]
    assert h["false_confident_rate"] == 0.5  # type: ignore[index]
    v = s["verification"]
    assert v["citation_validity"] == 1.0  # type: ignore[index]
    assert v["relevant_source_fraction"] == 0.5  # type: ignore[index]


def test_unreviewed_labels_never_produce_an_accuracy() -> None:
    items = [EvalItem(id="1", ground_truth_claim="a", claim_type="factual",
                      reference_verdict="supported")]  # fmt: skip
    records = [EvalRecord(id="1", predicted_verdict="supported")]
    s = summarize(items, records)
    assert s["human_verified"]["items"] == 0  # type: ignore[index]
    assert s["human_verified"]["verdict_accuracy"] is None  # type: ignore[index]
    assert "verdict_accuracy" not in s["verification"]  # type: ignore[operator]
    assert s["provisional_unreviewed_labels"]["verdict_accuracy"] == 1.0  # type: ignore[index]


def test_human_verified_requires_reviewer_and_date() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        EvalItem(id="1", ground_truth_claim="a", claim_type="factual",
                 reference_verdict="supported", review_status="human_verified")  # fmt: skip


def test_example_dataset_is_valid() -> None:
    lines = (REPO / "eval" / "dataset.example.jsonl").read_text(encoding="utf-8").splitlines()
    items = [EvalItem.model_validate(json.loads(line)) for line in lines if line.strip()]
    assert items, "example dataset should not be empty"
    assert len({i.id for i in items}) == len(items)
