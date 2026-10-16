"""scripts/verify_claim.py wiring with a fake model (no network)."""

import argparse
import importlib.util
import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.errors import ProviderError
from tests.conftest import ANALYSIS_FACTUAL, FakeLLM, make_grounded

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_claim.py"
spec = importlib.util.spec_from_file_location("verify_claim", SCRIPT)
assert spec and spec.loader
vc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vc)

RUN = {
    "results": [
        {
            "case": {
                "id": "en-debate-1200",
                "youtube_url": "https://www.youtube.com/watch?v=VgsC_aBquUE",
                "timestamp_sec": 1200,
                "language": "en",
            },
            "extraction": {
                "status": "success",
                "model": "gemini-3.6-flash",
                "claim": "Trump said he would vote to support the six-week ban.",
                "context": "Moderator question in the 2024 ABC debate.",
                "claim_type": "factual",
                "evidence_from_video": "you reversed course",
                "is_exact_quote": True,
                "window": {"start_sec": 1155, "end_sec": 1210},
                "latency_ms": 9725,
                "usage": {"prompt_tokens": 3576},
            },
        }
    ]
}


def _args(**kw: object) -> argparse.Namespace:
    base = {
        "from_run": None,
        "case": None,
        "claim": None,
        "context": None,
        "language": "auto",
        "id": None,
    }
    return argparse.Namespace(**{**base, **kw})


def test_saved_extraction_is_reused_and_quote_marked_unverified(tmp_path: Path) -> None:
    run = tmp_path / "results.json"
    run.write_text(json.dumps(RUN))
    item = vc.load_input(_args(from_run=str(run), case="en-debate-1200"))
    assert item["input_method"] == "video_extraction"
    assert item["context"] == "Moderator question in the 2024 ABC debate."
    assert item["extraction"]["model"] == "gemini-3.6-flash"
    assert item["extraction"]["quote_verified"] is False
    assert item["extraction"]["speaker_verified"] is False


def test_manual_claim_is_labelled_manual() -> None:
    item = vc.load_input(_args(claim="The Eiffel Tower was completed in 1889.", id="eiffel"))
    assert item["input_method"] == "manual" and item["video"] is None


async def test_successful_run_produces_reviewable_artifact(settings: Settings) -> None:
    llm = FakeLLM(
        json_responses={
            "ClaimAnalysisOutput": ANALYSIS_FACTUAL,
            "EvaluationOutput": {
                "source_assessments": [{"source_id": "S1", "stance": "contradicts", "note": "n"}],
                "verdict": "contradicted",
                "explanation": "Growth [S1].",
                "key_findings": [],
                "limitations": [],
            },
        },
        grounded=make_grounded(
            chunks=[("https://www.bls.gov/x", "bls.gov", "bls.gov")],
            supports=[("Jobs grew.", (0,))],
        ),
    )
    item = vc.load_input(_args(claim="AI replaced 80% of engineers.", id="m"))
    resp, err, _ = await vc.run(item, settings, llm)
    assert err is None and resp is not None
    a = vc.artifact(item, resp, None, 1, {})
    assert a["verification"]["verdict"] == "contradicted"
    assert a["review"]["status"] == "unreviewed"
    md = vc.render_markdown(a)
    assert "**contradicted**" in md and "https://www.bls.gov/x" in md


async def test_provider_failure_is_recorded_not_turned_into_a_verdict(settings: Settings) -> None:
    llm = FakeLLM(
        json_responses={
            "ClaimAnalysisOutput": ProviderError("busy", diagnostics={"http_status": 503})
        }
    )
    item = vc.load_input(_args(claim="AI replaced 80% of engineers.", id="m"))
    resp, err, _ = await vc.run(item, settings, llm)
    assert resp is None and err is not None
    a = vc.artifact(item, None, err, 1, {})
    assert a["verification"]["error"] == "provider_error"
    assert "No verdict: provider_error" in vc.render_markdown(a)


def test_missing_input_is_rejected() -> None:
    with pytest.raises(SystemExit):
        vc.load_input(_args())


async def test_partial_progress_survives_a_later_failure(settings: Settings) -> None:
    # Reproduces the live run of 2026-10-14: analysis OK, grounding quota-limited.
    from app.core.errors import ProviderQuotaExhaustedError

    llm = FakeLLM(
        json_responses={"ClaimAnalysisOutput": ANALYSIS_FACTUAL},
        grounded=ProviderQuotaExhaustedError("quota", diagnostics={"http_status": 429}),
    )
    item = vc.load_input(_args(claim="AI replaced 80% of engineers.", id="m"))
    resp, err, _ = await vc.run(item, settings, llm)
    assert resp is None and err is not None and err.code == "provider_quota_exhausted"
    done = (err.diagnostics or {})["completed"]
    assert done["analysis"]["normalized_claim"] == ANALYSIS_FACTUAL["normalized_claim"]
    assert done["analysis"]["planned_queries"]
    assert done["models_used"] == {"analysis": "fake-model"}
    assert done["usage"]["prompt_tokens"] == 100 and done["retrieval"] is None
    md = vc.render_markdown(vc.artifact(item, None, err, 1, {}))
    assert "Completed before the failure" in md and "planned_queries" in md


def test_committed_artifact_can_be_reused_without_re_extraction(tmp_path: Path) -> None:
    run = tmp_path / "results.json"
    run.write_text(json.dumps(RUN))
    item = vc.load_input(_args(from_run=str(run), case="en-debate-1200"))
    art = tmp_path / "artifact.json"
    art.write_text(json.dumps(vc.artifact(item, None, None, 0, {})))
    again = vc.load_input(_args(from_run=str(art)))
    assert again == item


def test_reference_score_is_provisional_for_the_ai_reviewed_debate_case() -> None:
    s = vc.reference_score("en-debate-1200", "contradicted")
    assert s is not None
    assert s["false_confident"] is True and s["counted"] is False
    assert s["reference_review_status"] == "ai_reviewed"
    assert vc.reference_score("en-debate-1200", None)["outcome"] == "no_verdict"
    assert vc.reference_score("no-such-case", "supported") is None


async def test_artifact_and_sheet_carry_the_reference_score(settings: Settings) -> None:
    llm = FakeLLM(
        json_responses={
            "ClaimAnalysisOutput": ProviderError("busy", diagnostics={"http_status": 503})
        }
    )
    item = vc.load_input(_args(claim="x claim text", id="en-debate-1200"))
    resp, err, _ = await vc.run(item, settings, llm)
    a = vc.artifact(item, resp, err, 1, {})
    assert a["reference_score"]["outcome"] == "no_verdict"
    assert "PROVISIONAL" in vc.render_markdown(a)
