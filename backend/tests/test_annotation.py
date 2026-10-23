"""Annotation cases: machine proposals never become labels; only verified cases are exported."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.evaluation.annotation import AnnotationCase

REPO = Path(__file__).resolve().parents[2]
CASES = REPO / "eval" / "annotation" / "cases.jsonl"
BASE: dict[str, Any] = {
    "id": "en-x",
    "youtube_url": "https://www.youtube.com/watch?v=VgsC_aBquUE",
    "timestamp_sec": 1200,
    "language": "en",
    "machine": {"source": "live_extraction", "claim": "Model's claim", "claim_type": "factual"},
}
DONE: dict[str, Any] = {
    "exact_words": "you said you would vote to support the six-week ban",
    "speaker": "Linsey Davis (moderator)",
    "claim": "The moderator said Trump would vote to support the six-week ban.",
    "claim_type": "factual",
    "reference_verdict": "supported",
    "evidence": [{"url": "https://example.org/transcript"}],
}


def test_unreviewed_case_is_not_ground_truth() -> None:
    case = AnnotationCase.model_validate({**BASE, "human": DONE})
    assert case.to_eval_item() is None  # complete, but nobody has signed it off


def test_verified_case_needs_reviewer_date_and_fields() -> None:
    with pytest.raises(ValidationError):
        AnnotationCase.model_validate({**BASE, "human": {**DONE, "status": "human_verified"}})
    with pytest.raises(ValidationError):
        AnnotationCase.model_validate(
            {
                **BASE,
                "human": {"status": "human_verified", "reviewer": "r", "reviewed_on": "2026-10-19"},
            }
        )


def test_export_uses_human_labels_never_the_machine_proposal() -> None:
    human = {**DONE, "status": "human_verified", "reviewer": "r", "reviewed_on": "2026-10-19"}
    item = AnnotationCase.model_validate({**BASE, "human": human}).to_eval_item()
    assert item is not None
    assert item.ground_truth_claim == DONE["claim"] != BASE["machine"]["claim"]
    assert item.video_id == "VgsC_aBquUE" and item.review_status == "human_verified"


def test_confident_reference_verdict_needs_evidence() -> None:
    case = AnnotationCase.model_validate({**BASE, "human": {**DONE, "evidence": []}})
    assert "evidence" in case.human.missing()


def test_shipped_cases_are_real_moments_and_all_unreviewed() -> None:
    cases = [
        AnnotationCase.model_validate(json.loads(x))
        for x in CASES.read_text("utf-8").splitlines()
        if x
    ]
    assert len(cases) >= 6 and len({c.id for c in cases}) == len(cases)
    assert all(c.human.status == "unreviewed" and c.human.claim is None for c in cases)
