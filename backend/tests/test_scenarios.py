"""Evidence-to-verdict scenarios through the real pipeline (analysis -> retrieval -> evaluation ->
safeguards), with a scripted model and synthetic sources on example.* domains.

Most scenarios script an overconfident or mistaken model and check what the deterministic
decision logic does with it; a few check that a correct model output passes through unchanged.
Whether a real model produces these outputs is a question for live evaluation, not these tests.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import Settings
from app.core.errors import RetrievalUnavailableError
from app.models.schemas import VerifyRequest, VerifyResponse
from app.services.citations import EvidenceSource
from app.services.retrieval import RetrievalResult
from app.services.verification import VerificationService
from tests.conftest import ANALYSIS_FACTUAL, FakeLLM, judge_passages


def src(sid: str, site: str, text: str, origin: str = "page_text") -> EvidenceSource:
    return EvidenceSource(
        sid, f"https://{site}/{sid.lower()}", f"{site} report", site, [text], origin=origin
    )  # type: ignore[arg-type]


class Retriever:
    name = "scenario"

    def __init__(self, sources: list[EvidenceSource] | Exception) -> None:
        self.sources = sources

    async def retrieve(self, **kw: object) -> RetrievalResult:
        if isinstance(self.sources, Exception):
            raise self.sources
        return RetrievalResult(sources=self.sources, executed_queries=["q"], backend="scenario")


def evaluation(
    verdict: str,
    stances: dict[str, str],
    findings: list[tuple[str, str, list[str]]] = (),  # type: ignore[assignment]
) -> dict[str, Any]:
    return {
        "source_assessments": [
            {"source_id": k, "stance": v, "note": "n"} for k, v in stances.items()
        ],
        "verdict": verdict,
        "explanation": "Scripted explanation.",
        "key_findings": [{"text": t, "stance": s, "source_ids": ids} for t, s, ids in findings],
        "limitations": [],
    }


async def run(
    settings: Settings,
    claim: str,
    sources: list[EvidenceSource] | Exception,
    model_eval: dict[str, Any] | None,
    analysis: dict[str, Any] | None = None,
    entailment: dict[str, Any] | None = None,
) -> VerifyResponse:
    responses: dict[str, Any] = {"ClaimAnalysisOutput": analysis or ANALYSIS_FACTUAL}
    if entailment is not None:
        responses["EntailmentOutput"] = entailment
    if model_eval is not None:
        responses["EvaluationOutput"] = model_eval
    llm = FakeLLM(json_responses=responses)
    return await VerificationService(llm, settings, retriever=Retriever(sources)).verify(
        VerifyRequest(claim=claim)
    )


def rules(resp: VerifyResponse) -> list[str]:
    return [a.rule for a in resp.verdict_adjustments]


# --------------------------------------------------------------- attribution and quotation


async def test_false_speaker_attribution(settings: Settings) -> None:
    """The quoted words exist, but they are someone else's."""
    resp = await run(
        settings,
        "Senator Jane Roe said she would vote to keep the ban.",
        [
            src(
                "S1",
                "transcripts.example.org",
                "MODERATOR: Your vote? DOE: I will vote to keep the ban.",
            )
        ],
        evaluation("supported", {"S1": "supports"}),
    )
    assert resp.verdict == "insufficient_evidence"
    assert rules(resp) == ["attribution_without_direct_evidence"]


async def test_fabricated_quotation(settings: Settings) -> None:
    resp = await run(
        settings,
        'Mayor Doe said "we will close every library by June".',
        [
            src(
                "S1",
                "news.example.org",
                'Doe said: "We will review library opening hours in June."',
            )
        ],
        evaluation("supported", {"S1": "supports"}),
    )
    assert resp.verdict == "insufficient_evidence"
    assert rules(resp) == ["exact_quote_not_confirmed"]


async def test_correct_quote_with_misleading_context(settings: Settings) -> None:
    """The quote is real, but she was describing the opposition's plan, not stating her own.

    The words are authentic (found verbatim, in her transcript line), but she did not say them
    as her own statement, so they do not confirm the attribution: no confident verdict.
    """
    page = (
        'Doe said: "taxes will rise next year" and immediately added that this was the '
        "opposition plan she would vote against."
    )
    resp = await run(
        settings,
        'Doe said "taxes will rise next year".',
        [
            src("S1", "transcripts.example.org", f"DOE: {page}"),
            src(
                "S2", "factcheck.example.net", "In context Doe was describing the opposition plan."
            ),
        ],
        evaluation(
            "supported",
            {"S1": "supports", "S2": "contradicts"},
            [
                ("She said these words [S1].", "supports", ["S1"]),
                ("She was describing a plan she opposed [S2].", "contradicts", ["S2"]),
            ],
        ),
        # A correct reader: the words appear, but as a description of someone else's plan.
        entailment=judge_passages("neutral"),
    )
    assert resp.verdict == "insufficient_evidence"
    assert rules(resp) == ["attribution_not_entailed"]


async def test_syndicated_copies_do_not_confirm_a_quote(settings: Settings) -> None:
    text = 'Doe told reporters: "I will vote to keep the ban in place" on Friday in Tampa.'
    resp = await run(
        settings,
        "Doe said she would vote to keep the ban.",
        [
            src("S1", "wire.example.org", text, origin="search_snippet"),
            src("S2", "partner.example.net", text, origin="search_snippet"),
        ],
        evaluation("supported", {"S1": "supports", "S2": "supports"}),
    )
    assert resp.verdict == "insufficient_evidence"
    assert resp.independent_source_count == 1


# --------------------------------------------------------------- compound and partial claims


async def test_true_fact_with_unsupported_cause(settings: Settings) -> None:
    resp = await run(
        settings,
        "Unemployment fell in 2023 because of the new tax law.",
        [
            src("S1", "stats.example.gov", "Unemployment fell from 4.1% to 3.7% during 2023."),
            src(
                "S2",
                "research.example.edu",
                "The study finds no measurable effect of the tax law on unemployment.",
            ),
        ],
        evaluation(
            "supported",
            {"S1": "supports", "S2": "contradicts"},
            [
                ("Unemployment fell in 2023 [S1].", "supports", ["S1"]),
                ("No evidence the tax law caused it [S2].", "contradicts", ["S2"]),
            ],
        ),
    )
    assert resp.verdict == "mixed"
    assert rules(resp) == ["verdict_contradicts_own_findings"]
    assert resp.explanation.startswith("The evidence supports part of this claim")


async def test_partial_truth_stays_mixed(settings: Settings) -> None:
    resp = await run(
        settings,
        "The bridge opened in 2019 and cost 40 million dollars.",
        [
            src("S1", "city.example.gov", "The bridge opened in May 2019."),
            src("S2", "audit.example.org", "The final cost of the bridge was 62 million dollars."),
        ],
        evaluation("mixed", {"S1": "supports", "S2": "contradicts"}),
    )
    assert resp.verdict == "mixed" and rules(resp) == []


async def test_numerically_wrong_statistic_contradicted_by_primary_data(settings: Settings) -> None:
    resp = await run(
        settings,
        "Unemployment doubled last year.",
        [src("S1", "stats.example.gov", "The unemployment rate rose from 3.5% to 3.9% last year.")],
        evaluation(
            "contradicted",
            {"S1": "contradicts"},
            [("It rose by 0.4 points, not double [S1].", "contradicts", ["S1"])],
        ),
    )
    assert resp.verdict == "contradicted" and rules(resp) == []


# --------------------------------------------------------------- evidence quality


async def test_headline_only_mention_is_not_evidence(settings: Settings) -> None:
    """The model calls an article supporting because its title repeats the claim; it never
    assessed any excerpt as backing. No backing source: no confident verdict."""
    resp = await run(
        settings,
        "The vaccine causes memory loss.",
        [src("S1", "blog.example.net", "Readers asked us about this rumour; we found no study.")],
        evaluation("supported", {"S1": "unclear"}),
    )
    assert resp.verdict == "insufficient_evidence"
    assert rules(resp) == ["verdict_without_backing_source"]


async def test_conflicting_primary_sources_are_kept_and_flagged(settings: Settings) -> None:
    resp = await run(
        settings,
        "The law took effect on 1 May 2024.",
        [
            src("S1", "legislature.example.gov", "The act takes effect on 1 May 2024."),
            src("S2", "courts.example.gov", "Enforcement of the act was stayed until 1 July 2024."),
        ],
        evaluation("supported", {"S1": "supports", "S2": "contradicts"}),
    )
    assert {s.id for s in resp.sources} == {"S1", "S2"}  # nothing silently dropped
    assert any("contradict the claim" in n for n in resp.guardrail_notes)


async def test_all_results_irrelevant(settings: Settings) -> None:
    resp = await run(
        settings,
        "The museum reopened in 2021.",
        [src("S1", "travel.example.org", "Ten things to eat in the old town.")],
        evaluation("contradicted", {"S1": "irrelevant"}),
    )
    assert resp.verdict == "insufficient_evidence"


async def test_missing_evidence_gives_no_verdict_call(settings: Settings) -> None:
    resp = await run(settings, "The museum reopened in 2021.", [], None)
    assert resp.verdict == "insufficient_evidence" and resp.sources == []


async def test_failed_search_is_an_error_not_insufficient_evidence(settings: Settings) -> None:
    with pytest.raises(RetrievalUnavailableError):
        await run(settings, "The museum reopened in 2021.", RetrievalUnavailableError("down"), None)


async def test_opinion_gets_no_factual_verdict(settings: Settings) -> None:
    resp = await run(
        settings,
        "This is the greatest invention of all time.",
        [],
        None,
        analysis={**ANALYSIS_FACTUAL, "claim_type": "opinion", "is_checkworthy": False},
    )
    assert resp.verdict == "not_verifiable" and resp.sources == []
