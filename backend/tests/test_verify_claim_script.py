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
