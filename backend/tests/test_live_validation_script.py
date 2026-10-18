"""Mechanical test of scripts/live_validation.py with a fake model (no network).

Proves the harness wiring and report rendering work, so the first live run is
spent on measurement rather than debugging. It says nothing about live accuracy.
"""

import argparse
import importlib.util
import json
from pathlib import Path

import httpx

from app.core.config import Settings
from app.evaluation.live import LiveCase
from app.services.llm import Usage
from tests.conftest import ANALYSIS_FACTUAL, EXTRACTION_OK, FakeLLM, make_grounded

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "live_validation.py"
spec = importlib.util.spec_from_file_location("live_validation", SCRIPT)
assert spec and spec.loader
lv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lv)

CASE = LiveCase(
    id="en-test",
    youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    timestamp_sec=755,
    language="en",
    duration_sec=1200,
    ground_truth_claim="Artificial intelligence has already replaced 80% of software engineers.",
    verified_by="test",
    reference_verdict="contradicted",
)


class ScalingFake(FakeLLM):
    """Media tokens proportional to the clip length, as if clipping were honoured."""

    async def generate_json(self, **kw):  # type: ignore[no-untyped-def]
        res = await super().generate_json(**kw)
        video = kw.get("video")
        if video is None:
            return res
        secs = video.end_sec - video.start_sec
        usage = Usage(
            prompt_tokens=100 * secs,
            output_tokens=50,
            total_tokens=100 * secs + 50,
            video_tokens=60 * secs,
            audio_tokens=32 * secs,
        )
        return type(res)(text=res.text, usage=usage, model=res.model)


async def test_run_case_and_report(settings: Settings) -> None:
    llm = ScalingFake(
        json_responses={
            "ExtractionOutput": EXTRACTION_OK,
            "ClaimAnalysisOutput": ANALYSIS_FACTUAL,
            "EvaluationOutput": {
                "source_assessments": [{"source_id": "S1", "stance": "contradicts", "note": "n"}],
                "verdict": "contradicted",
                "explanation": "Employment grows [S1].",
                "key_findings": [],
                "limitations": [],
            },
        },
        grounded=make_grounded(
            chunks=[("https://www.bls.gov/ooh/x", "bls.gov", "bls.gov")],
            supports=[("Employment of developers is projected to grow 17 percent.", (0,))],
        ),
    )

    def page(req: httpx.Request) -> httpx.Response:
        body = (
            "<html><p>Employment of software developers is projected to grow 17 percent.</p></html>"
        )
        return httpx.Response(200, html=body)

    args = argparse.Namespace(
        skip_clipping=False, skip_verify=False, verify_from="ground_truth", fetch_sources=True
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(page)) as http:
        result = await lv.run_case(CASE, llm, settings, http, args)

    assert result["extraction"]["claim_f1"] == 1.0
    assert result["extraction"]["context"] == EXTRACTION_OK["context"]
    assert result["clipping"]["verdict"] == "clipping_honoured"
    assert [r["window_sec"] for r in result["clipping"]["runs"]] == [20, 50]
    v = result["verification"]
    assert v["verdict"] == "contradicted"
    assert v["context_sent"] is None  # ground-truth claims carry no model context
    assert v["sources"][0]["page_check"]["fetched"] is True
    assert v["sources"][0]["page_check"]["needs_manual_review"] is False

    meta = {
        "run_at": "t",
        "extraction_model": "m",
        "verification_model": "m",
        "window": "w",
        "fps": 0.5,
        "resolution": "low",
        "prices": {},
        "preflight": "skipped",
    }
    report = lv.render_report(meta, [result])
    assert "clipping_honoured" in report
    assert "| en-test |" in report
    assert "supports conclusion?" in report
    json.dumps(result)  # results.json must be serializable


async def test_errors_are_recorded_not_raised(settings: Settings) -> None:
    from app.core.errors import VideoUnavailableError

    llm = FakeLLM(json_responses={"ExtractionOutput": VideoUnavailableError("private")})
    args = argparse.Namespace(
        skip_clipping=False, skip_verify=True, verify_from="ground_truth", fetch_sources=False
    )
    async with httpx.AsyncClient() as http:
        result = await lv.run_case(CASE, llm, settings, http, args)
    assert result["extraction"]["error"] == "video_unavailable"
    assert result["clipping"]["error"] == "video_unavailable"
    assert "video_unavailable" in lv.render_report(
        {
            "run_at": "t",
            "extraction_model": "m",
            "verification_model": "m",
            "window": "w",
            "fps": 0.5,
            "resolution": "low",
            "prices": {},
            "preflight": "skipped",
        },
        [result],
    )


async def test_exploratory_case_verifies_extracted_claim(settings: Settings) -> None:
    case = CASE.model_copy(update={"ground_truth_claim": None, "verified_by": None})
    assert case.exploratory
    llm = FakeLLM(
        json_responses={
            "ExtractionOutput": EXTRACTION_OK,
            "ClaimAnalysisOutput": {**ANALYSIS_FACTUAL, "claim_type": "opinion"},
        }
    )
    args = argparse.Namespace(
        skip_clipping=True, skip_verify=False, verify_from="ground_truth", fetch_sources=False
    )
    async with httpx.AsyncClient() as http:
        result = await lv.run_case(case, llm, settings, http, args)
    assert result["extraction"]["claim_f1"] is None
    assert result["verification"]["claim_verified"] == EXTRACTION_OK["claim"]
    assert result["verification"]["context_sent"] == EXTRACTION_OK["context"]
    assert "(exploratory)" in lv.render_report(
        {
            "run_at": "t",
            "extraction_model": "m",
            "verification_model": "m",
            "window": "w",
            "fps": 0.5,
            "resolution": "low",
            "prices": {},
            "preflight": "skipped",
        },
        [result],
    )


def test_repo_live_cases_file_is_valid() -> None:
    path = Path(__file__).resolve().parents[2] / "eval" / "live_cases.jsonl"
    cases = [
        LiveCase.model_validate(json.loads(x))
        for x in path.read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]
    assert {c.language for c in cases} == {"en", "ru"}
    assert not any(c.is_placeholder() for c in cases)


async def test_failed_stage_keeps_diagnostics_and_is_listed_separately(settings: Settings) -> None:
    from app.core.errors import ProviderQuotaExhaustedError

    diag = {
        "operation": "video_extraction",
        "http_status": 429,
        "provider_status": "RESOURCE_EXHAUSTED",
        "quota_violations": [{"id": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}],
        "retry_delay": "79080s",
    }
    llm = FakeLLM(
        json_responses={
            "ExtractionOutput": ProviderQuotaExhaustedError("used up", diagnostics=diag)
        }
    )
    args = argparse.Namespace(
        skip_clipping=True, skip_verify=False, verify_from="ground_truth", fetch_sources=False
    )
    case = CASE.model_copy(update={"ground_truth_claim": None, "verified_by": None})
    async with httpx.AsyncClient() as http:
        result = await lv.run_case(case, llm, settings, http, args)
    assert result["extraction"]["diagnostics"] == diag
    assert "claim" not in result["extraction"]  # no fallback claim is substituted
    assert result["verification"]["error"] == "no_claim"
    report = lv.render_report(
        {
            "run_at": "t",
            "extraction_model": "m",
            "verification_model": "m",
            "window": "w",
            "fps": 0.5,
            "resolution": "low",
            "prices": {},
            "preflight": "skipped",
        },
        [result],
    )
    failed = report.split("## Failed stages")[1].split("## Source review sheet")[0]
    assert "| en-test | extraction | provider_quota_exhausted | video_extraction | 429 |" in failed
    assert "GenerateRequestsPerDayPerProjectPerModel-FreeTier" in failed
    json.dumps(result)


async def test_run_case_uses_the_configured_retriever(settings: Settings) -> None:
    from app.services.citations import EvidenceSource
    from app.services.retrieval import RetrievalResult

    class Search:
        name = "search"

        async def retrieve(self, **kw: object) -> RetrievalResult:
            return RetrievalResult(
                sources=[
                    EvidenceSource(
                        "S1",
                        "https://www.bls.gov/x",
                        "BLS",
                        "bls.gov",
                        ["Jobs grew."],
                        origin="page_text",
                    )
                ],
                executed_queries=["q"],
                backend="brave",
            )

    llm = FakeLLM(
        json_responses={
            "ExtractionOutput": EXTRACTION_OK,
            "ClaimAnalysisOutput": ANALYSIS_FACTUAL,
            "EvaluationOutput": {
                "source_assessments": [{"source_id": "S1", "stance": "contradicts", "note": "n"}],
                "verdict": "contradicted",
                "explanation": "Jobs grew [S1].",
                "key_findings": [],
                "limitations": [],
            },
        }
    )
    args = argparse.Namespace(
        skip_clipping=True, skip_verify=False, verify_from="extracted", fetch_sources=False
    )
    async with httpx.AsyncClient() as http:
        result = await lv.run_case(CASE, llm, settings, http, args, Search())
    assert "grounded_search" not in [name for name, _ in llm.calls]
    assert result["verification"]["sources"][0]["excerpt_origin"] == "page_text"
