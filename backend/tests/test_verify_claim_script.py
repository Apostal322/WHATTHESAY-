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
        "proposition": None,
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


# ------------------------------------------- provenance for the first live run (mechanics only)

ATTRIBUTION_ANALYSIS = {**ANALYSIS_FACTUAL, "attributed_to": "Donald Trump"}
ATTRIBUTION_EVAL = {
    "source_assessments": [
        {"source_id": "S1", "stance": "supports", "note": "n"},
        {"source_id": "S2", "stance": "supports", "note": "n"},
    ],
    "verdict": "supported",
    "explanation": "He said so [S1][S2].",
    "key_findings": [],
    "limitations": [],
}


def _attribution_llm(entailment: object) -> FakeLLM:
    return FakeLLM(
        json_responses={
            "ClaimAnalysisOutput": ATTRIBUTION_ANALYSIS,
            "EvaluationOutput": ATTRIBUTION_EVAL,
            "EntailmentOutput": entailment,
        },
        # Grounding excerpts are summaries: the speaker's words must come from two independent
        # origins before the entailment check runs at all.
        grounded=make_grounded(
            chunks=[
                ("https://www.foxnews.com/politics/x", "Fox News", "foxnews.com"),
                ("https://apnews.com/article/y", "AP News", "apnews.com"),
            ],
            supports=[
                ('Trump said of Amendment 4: "I will be voting no."', (0,)),
                ('Trump told Fox News: "I will be voting no" on Amendment 4.', (1,)),
            ],
        ),
    )


async def test_every_model_call_is_recorded_exactly(settings: Settings) -> None:
    from tests.conftest import judge_passages

    llm = _attribution_llm(judge_passages("entails"))
    item = vc.load_input(
        _args(claim="In late August 2024 Donald Trump said he would vote against Amendment 4.",
              id="en-debate-1200-p2", statement_date="2024-08", video_published_date=None)
    )  # fmt: skip
    calls: list[dict[str, object]] = []
    resp, err, _ = await vc.run(item, settings, llm, calls=calls)
    assert err is None and resp is not None
    assert [c["operation"] for c in calls] == [
        "ClaimAnalysisOutput", "grounded_search", "EvaluationOutput", "EntailmentOutput",
    ]  # fmt: skip
    for c in calls:  # exact prompt, raw output, usage, latency, model for each call
        assert c["prompt"] and c["raw_output"] is not None and c["usage"] and c["model_served"]
        assert isinstance(c["latency_ms"], int)
    assert calls[1]["grounding"]["sources"][0]["uri"] == "https://www.foxnews.com/politics/x"
    assert resp.attribution_check is not None and len(resp.attribution_check.passages) == 2
    assert '"I will be voting no."' in str(calls[3]["prompt"])  # what the check saw
    assert "When it was said: 2024-08" in str(calls[0]["prompt"])  # the date reached the model

    a = vc.artifact(item, resp, None, 5, {"live": False}, calls, "en-debate-1200:P2")
    assert a["model_calls_summary"]["model_calls"] == 4
    assert a["reference_score"]["reference"] == "en-debate-1200:P2"
    assert a["reference_score"]["counted"] is False  # AI-reviewed reference: provisional
    assert a["verification"]["attribution_check"]["status"] == "confirmed"
    md = vc.render_markdown(a)
    for heading in ("Speaker's-words check", "Model calls", "Review checklist", "Safeguards"):
        assert heading in md


async def test_a_failed_call_is_recorded_with_its_error(settings: Settings) -> None:
    from app.core.errors import ProviderQuotaExhaustedError

    llm = _attribution_llm(ProviderQuotaExhaustedError("quota", diagnostics={"http_status": 429}))
    item = vc.load_input(
        _args(claim="Donald Trump said he would vote against Amendment 4.", id="m")
    )
    calls: list[dict[str, object]] = []
    resp, err, _ = await vc.run(item, settings, llm, calls=calls)
    assert err is None and resp is not None
    assert resp.verdict == "insufficient_evidence"  # the check failed: no confident verdict
    assert calls[-1]["error"]["code"] == "provider_quota_exhausted"  # type: ignore[index]
    md = vc.render_markdown(vc.artifact(item, resp, None, 5, {}, calls))
    assert "provider_quota_exhausted" in md and "failure `provider_error:" in md


def test_an_artifact_containing_the_api_key_is_never_written(tmp_path: Path) -> None:
    item = vc.load_input(_args(claim="AI replaced 80% of engineers.", id="m"))
    leaked = vc.artifact(item, None, None, 0, {"note": "AIzaFAKE-not-a-real-key-123"})
    with pytest.raises(SystemExit):
        vc.write_outputs(leaked, "AIzaFAKE-not-a-real-key-123", tmp_path)  # secret-scan: allow
    assert list(tmp_path.iterdir()) == []
    assert vc.write_outputs(leaked, "a-different-key-456", tmp_path)  # secret-scan: allow


# ------------------------------------------ experiment controls: ledger, propositions, output

PRICES = {"input_per_M": 0.75, "output_per_M": 3.75, "search_per_1k": 14.0}


def _budgeted(settings: Settings) -> Settings:
    return settings.model_copy(update={"max_retries": 0, "fallback_model": None})


async def test_ledger_counts_every_call_across_processes(
    tmp_path: Path, settings: Settings
) -> None:
    from app.core.errors import ProviderQuotaExhaustedError

    path = tmp_path / "call-ledger.json"
    ledger = vc.CallLedger(path, 8, 1.0, PRICES)
    item = vc.load_input(
        _args(claim="Donald Trump said he would vote against Amendment 4.", id="a")
    )
    llm = _attribution_llm(ProviderQuotaExhaustedError("quota"))  # the 4th call fails
    await vc.run(item, _budgeted(settings), llm, calls=[], ledger=ledger)
    # A new process reads the same file: the budget does not reset on restart.
    again = vc.CallLedger(path, 8, 1.0, PRICES)
    status = again.status()
    assert (status["used_calls"], status["remaining_calls"], status["failed_calls"]) == (4, 4, 1)
    failed = again.entries[-1]
    assert failed["status"] == "error" and failed["est_cost_usd"] is None  # never assumed free
    assert again.entries[1]["search_queries"] == 1  # grounding queries are costed
    assert 0 < status["estimated_spend_usd"] < 1
    stored = {k for e in again.entries for k in e}
    assert not stored & {"prompt", "raw_output", "grounding"}  # no prompts or outputs kept


async def test_a_run_does_not_start_without_room_for_all_its_calls(
    tmp_path: Path, settings: Settings
) -> None:
    ledger = vc.CallLedger(tmp_path / "l.json", 5, 1.0, PRICES)
    for _ in range(2):
        ledger.settle(ledger.reserve("x", "ClaimAnalysisOutput", "m"), {"usage": {}})
    item = vc.load_input(_args(claim="AI replaced 80% of engineers.", id="m"))
    with pytest.raises(SystemExit, match="only 3 calls left"):
        await vc.run(item, _budgeted(settings), FakeLLM(), calls=[], ledger=ledger)


def test_ledger_refuses_a_call_beyond_the_budget(tmp_path: Path) -> None:
    ledger = vc.CallLedger(tmp_path / "l.json", 1, 1.0, PRICES)
    ledger.reserve("x", "ClaimAnalysisOutput", "m")
    with pytest.raises(vc.ExperimentBudgetExhausted):
        ledger.reserve("x", "EvaluationOutput", "m")


@pytest.mark.parametrize(
    ("update", "problem"),
    [
        ({"max_retries": 2}, "MAX_RETRIES must be 0"),
        ({"fallback_model": "other"}, "FALLBACK_MODEL must be unset"),
    ],
)
def test_budgeted_runs_refuse_invisible_requests(
    tmp_path: Path, settings: Settings, update: dict[str, object], problem: str
) -> None:
    ledger = vc.CallLedger(tmp_path / "l.json", 8, 1.0, PRICES)
    s = _budgeted(settings).model_copy(update=update)
    assert any(problem in p for p in vc.ledger_preconditions(s, ledger))
    no_prices = vc.CallLedger(tmp_path / "l.json", 8, 1.0, {})
    assert any("--price-in" in p for p in vc.ledger_preconditions(_budgeted(settings), no_prices))


@pytest.mark.parametrize("pid", ["P2", "P5"])
def test_a_reference_proposition_is_checked_word_for_word(pid: str) -> None:
    ref = json.loads((vc.REFERENCE_DIR / "en-debate-1200.json").read_text(encoding="utf-8"))
    prop = next(p for p in ref["propositions"] if p["id"] == pid)
    item = vc.load_input(_args(proposition=f"en-debate-1200:{pid}"))
    assert item["claim"] == prop["text"] and item["input_method"] == "reference_proposition"
    assert item["id"] == f"en-debate-1200-{pid.lower()}" and item["context"] == ref["context"]
    assert item["proposition"]["reference_review_status"] == "ai_reviewed"


async def test_summarize_reports_counts_and_measured_figures(
    tmp_path: Path, settings: Settings
) -> None:
    from tests.conftest import judge_passages

    ledger = vc.CallLedger(tmp_path / "call-ledger.json", 8, 1.0, PRICES)
    item = vc.load_input(
        _args(claim="Donald Trump said he would vote against Amendment 4.", id="a")
    )
    calls: list[dict[str, object]] = []
    resp, err, ms = await vc.run(
        item, _budgeted(settings), _attribution_llm(judge_passages("entails")), calls, ledger
    )
    vc.write_outputs(vc.artifact(item, resp, err, ms, {"live": False}, calls), "", tmp_path)
    m = vc.summarize(tmp_path)
    assert m["n_runs"] == 1 and m["ledger"]["calls"] == 4
    (r,) = m["runs"]
    assert (
        r["live"] is False and r["outcome"] == "supported" and r["model_calls"]["model_calls"] == 4
    )


def test_live_artifacts_go_to_a_git_ignored_directory() -> None:
    import subprocess

    probe = vc.OUT_DIR.relative_to(vc.REPO) / "x.json"
    out = subprocess.run(["git", "check-ignore", str(probe)], cwd=vc.REPO, capture_output=True)
    assert out.returncode == 0  # full prompts and raw outputs never reach a commit by default
