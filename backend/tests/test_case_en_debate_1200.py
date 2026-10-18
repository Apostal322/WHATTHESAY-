"""Regression tests for the real-world case en-debate-1200 (ABC debate, 10 Sep 2024).

OFFLINE FIXTURES, NOT LIVE VALIDATION. The model is scripted; what is tested is how the
application handles this case's shape (attribution vs. characterization, syndicated sources,
historical context), plus the integrity rules of its reviewed reference record. The live
attempts and their status are recorded in docs/live-validation-report.md.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings
from app.evaluation.reference import ReferenceRecord, score_verdict
from app.models.schemas import VerifyRequest
from app.services.citations import EvidenceSource
from app.services.retrieval import RetrievalResult
from app.services.verification import VerificationService
from tests.conftest import FakeLLM

REF_PATH = Path(__file__).resolve().parents[2] / "eval" / "reference" / "en-debate-1200.json"
REF = ReferenceRecord.model_validate(json.loads(REF_PATH.read_text(encoding="utf-8")))

CONTEXT = (
    "ABC News presidential debate, 10 September 2024. Moderator Linsey Davis to Donald Trump, "
    "about his late-August 2024 statements on Florida's six-week abortion law and Amendment 4."
)
ANALYSIS = {
    "normalized_claim": REF.claim,
    "claim_type": "factual",
    "is_checkworthy": True,
    "search_queries": [
        "Trump Florida six-week ban too short NBC August 2024",
        "Trump vote no Amendment 4 Fox News August 30 2024",
    ],
    "scope_notes": "Statements of 29-30 August 2024; judge as of 10 September 2024.",
    "reason": "",
}
# Fixture text written for these tests, modelled on how reports carry the quotations that
# secondary coverage attributes to Trump; NOT page captures (see eval/fixtures for those).
NBC = 'Trump told NBC News that six weeks is too short: "I am going to be voting that we need more than six weeks."'
# Paraphrase-only variants: what the same reports would say without the speaker's words.
NBC_PARAPHRASE = "Trump told NBC News that six weeks is too short and that he is going to be voting that we need more than six weeks."
FOX_PARAPHRASE = "In a Fox News interview on Friday, Aug. 30, 2024, Trump said he will be voting no on Amendment 4, the Florida abortion-rights ballot measure."
FOX = 'In a Fox News interview on Friday, Aug. 30, 2024, Trump said of Amendment 4, the Florida abortion-rights ballot measure: "I will be voting no."'
SYNDICATED = "Trump said Friday \"I will be voting no\" on Florida's Amendment 4, a day after saying the state's six-week limit was too short; the amendment would invalidate the six-week law."


def _sources() -> list[EvidenceSource]:
    return [
        EvidenceSource(
            "S1",
            "https://www.axios.com/2024/08/29/x",
            "Axios",
            "axios.com",
            [NBC],
            origin="page_text",
        ),
        EvidenceSource(
            "S2",
            "https://www.foxnews.com/politics/x",
            "Fox News",
            "foxnews.com",
            [FOX],
            origin="page_text",
        ),
        EvidenceSource(
            "S3",
            "https://www.catholicnewsagency.com/news/x",
            "CNA",
            "catholicnewsagency.com",
            [SYNDICATED],
            origin="page_text",
        ),
        EvidenceSource(
            "S4",
            "https://www.ewtnnews.com/world/x",
            "EWTN",
            "ewtnnews.com",
            [SYNDICATED],
            origin="page_text",
        ),
    ]


class Retriever:
    name = "search"

    async def retrieve(self, **kw: object) -> RetrievalResult:
        return RetrievalResult(
            sources=_sources(), executed_queries=ANALYSIS["search_queries"], backend="fixture"
        )


def _llm(evaluation: dict[str, object]) -> FakeLLM:
    return FakeLLM(json_responses={"ClaimAnalysisOutput": ANALYSIS, "EvaluationOutput": evaluation})


def _assess(stances: dict[str, str]) -> list[dict[str, str]]:
    return [{"source_id": sid, "stance": st, "note": "n"} for sid, st in stances.items()]


# ------------------------------------------------------------------ reference record integrity


def test_reference_record_is_ai_reviewed_and_never_counted_as_ground_truth() -> None:
    assert REF.review.status != "human_verified"
    assert REF.review.reviewer_kind == "ai_assistant"
    assert score_verdict("supported", REF)["counted"] is False


def test_unverified_wording_and_unopened_sources_are_labelled_as_such() -> None:
    assert REF.quote_status != "verified_verbatim"
    assert all(not e.opened_by_reviewer for e in REF.evidence)
    user = [e for e in REF.evidence if e.provenance == "user_supplied"]
    assert len(user) == 5 and all(not e.stances for e in user)  # supplied, never assessed unread
    assert not any(e.provenance == "app_retrieved" for e in REF.evidence)


def test_located_inspected_approved_and_interpreted_are_kept_apart() -> None:
    ev = {e.id: e for e in REF.evidence}
    # externally located by the user: URL and their description only
    for sid in ("U2", "U3", "U5"):
        assert ev[sid].located_by == "user" and ev[sid].located_on == "2026-10-14"
        assert ev[sid].user_statement and ev[sid].stance_basis == "none"
    assert "15:55" in (ev["U5"].user_statement or "")
    # nothing has been opened by the application or approved by a person
    assert not any(e.inspected_by_app or e.app_capture for e in REF.evidence)
    assert not any(e.human_approved for e in REF.evidence)
    # every recorded stance is labelled as an AI interpretation of a search summary
    with_stances = [e for e in REF.evidence if e.stances]
    assert with_stances and all(
        e.stance_basis == "ai_interpretation_of_search_summary" for e in with_stances
    )


def test_transcript_time_is_not_mapped_to_the_video() -> None:
    note = REF.transcript_timestamp_note
    assert "15:55" in note and "must not be read as" in note
    assert REF.video_timestamp_sec == 1200


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r["review"].update(status="human_verified"),  # AI cannot self-promote
        lambda r: r["evidence"][0].update(human_approved=True),
        lambda r: r["evidence"][0].update(inspected_by_app=True),  # no capture named
        lambda r: r["evidence"][-1].update(stance_basis="none"),  # stances need a basis
        lambda r: r["evidence"][-1].update(stance_basis="human_assessment"),
        lambda r: r["propositions"][2].update(temporal=None),
    ],
)
def test_reference_cannot_be_promoted_or_relabelled_without_the_facts(mutate: Any) -> None:
    raw = json.loads(REF_PATH.read_text(encoding="utf-8"))
    mutate(raw)
    with pytest.raises(ValueError):
        ReferenceRecord.model_validate(raw)


def test_a_human_reviewer_can_verify_the_record() -> None:
    raw = json.loads(REF_PATH.read_text(encoding="utf-8"))
    raw["review"].update(reviewer="A. Person", reviewer_kind="human", status="human_verified")
    raw["evidence"][0].update(human_approved=True)
    ref = ReferenceRecord.model_validate(raw)
    assert score_verdict("supported", ref)["counted"] is True


def test_criticism_of_the_limit_is_kept_apart_from_the_amendment_vote() -> None:
    kinds = {p.id: (p.kind, p.reference_verdict) for p in REF.propositions}
    assert kinds["P1"] == ("attribution", "supported")  # 'six weeks is too short'
    assert kinds["P2"] == ("attribution", "supported")  # 'voting no' on Amendment 4
    assert kinds["P4"] == ("characterization", "mixed")  # 'vote to support the six-week ban'
    assert kinds["P5"] == ("legal_effect", "supported")  # what a 'no' vote does in law
    assert REF.proposition("P5").primary_evidence == ["U3"]  # the official record
    assert REF.proposition("P3").temporal is not None


@pytest.mark.parametrize(
    ("verdict", "acceptable", "false_confident"),
    [
        ("supported", True, False),
        ("mixed", True, False),
        ("contradicted", False, True),
        ("insufficient_evidence", False, False),  # a miss, not a dangerous error
        ("not_verifiable", False, False),
    ],
)
def test_scoring_against_the_reference(
    verdict: str, acceptable: bool, false_confident: bool
) -> None:
    s = score_verdict(verdict, REF)  # type: ignore[arg-type]
    assert (s["acceptable"], s["false_confident"]) == (acceptable, false_confident)


def test_reference_rejects_inconsistent_records() -> None:
    raw = json.loads(REF_PATH.read_text(encoding="utf-8"))
    raw["acceptable_verdicts"] = ["mixed"]
    with pytest.raises(ValueError):
        ReferenceRecord.model_validate(raw)


# ------------------------------------------------------------------ pipeline behaviour on this case


async def test_context_with_speaker_and_date_reaches_analysis_and_evaluation(
    settings: Settings,
) -> None:
    llm = _llm(
        {
            "source_assessments": _assess(
                {"S1": "supports", "S2": "supports", "S3": "supports", "S4": "supports"}
            ),
            "verdict": "supported",
            "explanation": "He called six weeks too short [S1] and then said he would vote no [S2].",
            "key_findings": [],
            "limitations": [],
        }
    )
    await VerificationService(llm, settings, retriever=Retriever()).verify(
        VerifyRequest(claim=REF.claim, context=CONTEXT, language="en")
    )
    analysis_prompt, eval_prompt = llm.calls[0][1]["prompt"], llm.calls[-1][1]["prompt"]
    for prompt in (analysis_prompt, eval_prompt):
        assert "Linsey Davis" in prompt and "10 September 2024" in prompt
    assert "keep attributions" in eval_prompt


async def test_syndicated_report_counts_once_and_is_flagged_to_the_evaluator(
    settings: Settings,
) -> None:
    llm = _llm(
        {
            "source_assessments": _assess(
                {"S1": "supports", "S2": "supports", "S3": "supports", "S4": "supports"}
            ),
            "verdict": "supported",
            "explanation": "Supported [S1][S2].",
            "key_findings": [],
            "limitations": [],
        }
    )
    resp = await VerificationService(llm, settings, retriever=Retriever()).verify(
        VerifyRequest(claim=REF.claim, context=CONTEXT, language="en")
    )
    assert "same site or same original report as S4" in llm.calls[-1][1]["prompt"]
    assert resp.independent_source_count == 3  # Axios, Fox, CNA=EWTN
    assert score_verdict(resp.verdict, REF)["acceptable"]
    assert resp.verdict_adjustments == []  # the reports carry Trump's own words


async def test_paraphrase_only_reports_cannot_confirm_what_trump_said(settings: Settings) -> None:
    """Same claim and verdict as above, but every report only paraphrases him."""

    class ParaphraseRetriever:
        name = "search"

        async def retrieve(self, **kw: object) -> RetrievalResult:
            return RetrievalResult(
                sources=[
                    EvidenceSource(
                        "S1",
                        "https://www.axios.com/2024/08/29/x",
                        "Axios",
                        "axios.com",
                        [NBC_PARAPHRASE],
                        origin="page_text",
                    ),
                    EvidenceSource(
                        "S2",
                        "https://www.foxnews.com/politics/x",
                        "Fox News",
                        "foxnews.com",
                        [FOX_PARAPHRASE],
                        origin="page_text",
                    ),
                ],
                executed_queries=["q"],
                backend="fixture",
            )

    llm = _llm(
        {
            "source_assessments": _assess({"S1": "supports", "S2": "supports"}),
            "verdict": "supported",
            "explanation": "Supported [S1][S2].",
            "key_findings": [],
            "limitations": [],
        }
    )
    resp = await VerificationService(llm, settings, retriever=ParaphraseRetriever()).verify(
        VerifyRequest(claim=REF.claim, context=CONTEXT, language="en")
    )
    assert resp.verdict == "insufficient_evidence"
    assert [a.rule for a in resp.verdict_adjustments] == ["attribution_without_direct_evidence"]
    assert not score_verdict(resp.verdict, REF)["false_confident"]  # a miss, not a confident error


async def test_contradicted_verdict_without_contradicting_evidence_is_downgraded(
    settings: Settings,
) -> None:
    # A model that answers the *characterization* ("he never said he supports the ban") with a
    # confident 'contradicted' while its own stances only support the factual parts.
    llm = _llm(
        {
            "source_assessments": _assess(
                {
                    "S1": "supports",
                    "S2": "supports",
                    "S3": "partially_supports",
                    "S4": "partially_supports",
                }
            ),
            "verdict": "contradicted",
            "explanation": "Trump never said he supports the six-week ban.",
            "key_findings": [],
            "limitations": [],
        }
    )
    resp = await VerificationService(llm, settings, retriever=Retriever()).verify(
        VerifyRequest(claim=REF.claim, context=CONTEXT, language="en")
    )
    assert resp.verdict == "insufficient_evidence"
    assert not score_verdict(resp.verdict, REF)["false_confident"]


async def test_mixed_verdict_separating_effect_from_wording_is_kept(settings: Settings) -> None:
    llm = _llm(
        {
            "source_assessments": _assess(
                {
                    "S1": "supports",
                    "S2": "supports",
                    "S3": "partially_supports",
                    "S4": "partially_supports",
                }
            ),
            "verdict": "mixed",
            "explanation": "The reversal is documented [S1][S2]; 'support the ban' describes the effect of a no vote, not his words [S3].",
            "key_findings": [
                {
                    "text": "Called six weeks too short [S1].",
                    "stance": "supports",
                    "source_ids": ["S1"],
                },
                {
                    "text": "Said he would vote no on Amendment 4 [S2].",
                    "stance": "supports",
                    "source_ids": ["S2"],
                },
            ],
            "limitations": [],
        }
    )
    resp = await VerificationService(llm, settings, retriever=Retriever()).verify(
        VerifyRequest(claim=REF.claim, context=CONTEXT, language="en")
    )
    assert resp.verdict == "mixed"
    assert [f.source_ids for f in resp.key_findings] == [["S1"], ["S2"]]
    assert score_verdict(resp.verdict, REF)["acceptable"]
