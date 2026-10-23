"""Attribution safeguard: claims about what someone said need that person's own words.

Synthetic cases (example.* domains, invented people). The model is scripted; what is tested is
the deterministic verdict logic. A warning (guardrail note) and a safeguard (a recorded verdict
change in `verdict_adjustments`) are asserted separately.
"""

from typing import Any

from app.core.config import Settings
from app.models.schemas import VerifyRequest, VerifyResponse
from app.services.citations import EvidenceSource
from app.services.retrieval import RetrievalResult
from app.services.verification import VerificationService
from tests.conftest import FakeLLM, judge_passages

SAID_KEEP = "Senator Jane Doe said she would vote to keep the ban."
FACTUAL = "The ban was in force in September 2024."

CHARACTERIZATION = "Doe effectively votes to keep the ban, the outlet says in its analysis."
TRANSCRIPT = "MODERATOR: How will you vote? DOE: I will vote to keep the ban in place."
QUOTED_REPORT = 'Doe told reporters on Friday: "I will vote to keep the ban in place."'
QUOTED_REPORT_2 = 'Speaking in Tampa, Doe repeated: "I will vote to keep the ban in place."'
TRANSCRIPT_NO = "MODERATOR: How will you vote? DOE: I will vote against keeping the ban."
NEWS_FACT = "Records show the ban took effect in May 2024 and was in force in September."


def _src(sid: str, site: str, text: str, origin: str = "page_text") -> EvidenceSource:
    return EvidenceSource(sid, f"https://{site}/{sid}", site, site, [text], origin=origin)  # type: ignore[arg-type]


class Retriever:
    name = "search"

    def __init__(self, sources: list[EvidenceSource]) -> None:
        self.sources = sources

    async def retrieve(self, **kw: object) -> RetrievalResult:
        return RetrievalResult(sources=self.sources, executed_queries=["q"], backend="fixture")


def _analysis(claim: str, **extra: Any) -> dict[str, Any]:
    return {
        "normalized_claim": claim,
        "claim_type": "factual",
        "is_checkworthy": True,
        "reason": "r",
        "search_queries": ["q"],
        "scope_notes": None,
        **extra,
    }


async def _verify(
    settings: Settings,
    claim: str,
    sources: list[EvidenceSource],
    verdict: str,
    stances: dict[str, str],
    explanation: str = "See the sources [S1].",
    analysis: dict[str, Any] | None = None,
    entailment: dict[str, Any] | None = None,
) -> VerifyResponse:
    # By default the speaker's words say what the verdict says: true of these fixtures, whose
    # transcript lines and quotes are on point. Tests of the semantic stage pass their own.
    relation = {"supported": "entails", "contradicted": "contradicts"}.get(verdict, "neutral")
    llm = FakeLLM(
        json_responses={
            "EntailmentOutput": entailment or judge_passages(relation),
            "ClaimAnalysisOutput": analysis or _analysis(claim),
            "EvaluationOutput": {
                "source_assessments": [
                    {"source_id": s, "stance": st, "note": "n"} for s, st in stances.items()
                ],
                "verdict": verdict,
                "explanation": explanation,
                "key_findings": [],
                "limitations": [],
            },
        }
    )
    return await VerificationService(llm, settings, retriever=Retriever(sources)).verify(
        VerifyRequest(claim=claim, language="en")
    )


def _rules(resp: VerifyResponse) -> list[str]:
    return [a.rule for a in resp.verdict_adjustments]


# ----------------------------------------------------- the defect: characterization as speech


async def test_characterization_alone_cannot_confirm_what_someone_said(settings: Settings) -> None:
    resp = await _verify(
        settings,
        SAID_KEEP,
        [_src("S1", "news.example.com", CHARACTERIZATION)],
        "supported",
        {"S1": "supports"},
        'She said she would "vote to keep the ban" [S1].',
    )
    assert resp.verdict == "insufficient_evidence"
    assert _rules(resp) == ["attribution_without_direct_evidence"]
    adj = resp.verdict_adjustments[0]
    assert (adj.from_verdict, adj.to_verdict) == ("supported", "insufficient_evidence")


async def test_characterization_alone_cannot_refute_what_someone_said(settings: Settings) -> None:
    resp = await _verify(
        settings,
        "Senator Jane Doe said she would vote to repeal the ban.",
        [_src("S1", "news.example.com", CHARACTERIZATION)],
        "contradicted",
        {"S1": "contradicts"},
    )
    assert resp.verdict == "insufficient_evidence"
    assert _rules(resp) == ["attribution_without_direct_evidence"]


# ---------------------------------------------------------- the person's own words are enough


async def test_transcript_speaker_line_confirms_an_attribution(settings: Settings) -> None:
    resp = await _verify(
        settings,
        SAID_KEEP,
        [_src("S1", "transcripts.example.org", TRANSCRIPT)],
        "supported",
        {"S1": "supports"},
    )
    assert resp.verdict == "supported" and resp.verdict_adjustments == []


async def test_quoted_words_in_page_text_confirm_an_attribution(settings: Settings) -> None:
    resp = await _verify(
        settings,
        SAID_KEEP,
        [_src("S1", "wire.example.net", QUOTED_REPORT)],
        "supported",
        {"S1": "supports"},
    )
    assert resp.verdict == "supported" and resp.verdict_adjustments == []


async def test_transcript_can_refute_an_attribution(settings: Settings) -> None:
    resp = await _verify(
        settings,
        "Senator Jane Doe said she would vote to keep the ban.",
        [_src("S1", "transcripts.example.org", TRANSCRIPT_NO)],
        "contradicted",
        {"S1": "contradicts"},
    )
    assert resp.verdict == "contradicted" and resp.verdict_adjustments == []


async def test_direct_speech_must_be_in_a_source_backing_the_verdict(settings: Settings) -> None:
    # The transcript is present but assessed irrelevant; only the characterization supports.
    resp = await _verify(
        settings,
        SAID_KEEP,
        [
            _src("S1", "news.example.com", CHARACTERIZATION),
            _src("S2", "transcripts.example.org", TRANSCRIPT),
        ],
        "supported",
        {"S1": "supports", "S2": "irrelevant"},
    )
    assert resp.verdict == "insufficient_evidence"


# ------------------------------------------------- unverified quotes need independent evidence


async def test_one_snippet_quote_is_not_enough(settings: Settings) -> None:
    resp = await _verify(
        settings,
        SAID_KEEP,
        [_src("S1", "wire.example.net", QUOTED_REPORT, origin="search_snippet")],
        "supported",
        {"S1": "supports"},
    )
    assert resp.verdict == "insufficient_evidence"
    assert _rules(resp) == ["attribution_without_direct_evidence"]


async def test_two_independent_unverified_quotes_are_enough(settings: Settings) -> None:
    resp = await _verify(
        settings,
        SAID_KEEP,
        [
            _src("S1", "wire.example.net", QUOTED_REPORT, origin="search_snippet"),
            _src("S2", "paper.example.org", QUOTED_REPORT_2, origin="grounding_summary"),
        ],
        "supported",
        {"S1": "supports", "S2": "supports"},
    )
    assert resp.verdict == "supported" and resp.verdict_adjustments == []


async def test_syndicated_copies_of_one_quote_are_one_origin(settings: Settings) -> None:
    resp = await _verify(
        settings,
        SAID_KEEP,
        [
            _src("S1", "wire.example.net", QUOTED_REPORT, origin="search_snippet"),
            _src("S2", "wire.example.net", QUOTED_REPORT, origin="search_snippet"),
        ],
        "supported",
        {"S1": "supports", "S2": "supports"},
    )
    assert resp.verdict == "insufficient_evidence"


# --------------------------------------------------------------------- exact-quote claims


async def test_exact_quote_claim_needs_the_exact_words(settings: Settings) -> None:
    claim = 'Senator Jane Doe said "the ban goes much too far."'
    resp = await _verify(
        settings,
        claim,
        [_src("S1", "wire.example.net", 'Doe said: "The ban is far too strict for families."')],
        "supported",
        {"S1": "supports"},
    )
    assert resp.verdict == "insufficient_evidence"
    assert _rules(resp) == ["exact_quote_not_confirmed"]


async def test_exact_quote_found_verbatim_in_page_text_is_kept(settings: Settings) -> None:
    claim = 'Senator Jane Doe said "the ban goes much too far."'
    resp = await _verify(
        settings,
        claim,
        [
            _src(
                "S1",
                "wire.example.net",
                'Doe said on Friday: "The ban goes much too far," and left.',
            )
        ],
        "supported",
        {"S1": "supports"},
    )
    assert resp.verdict == "supported" and resp.verdict_adjustments == []


async def test_exact_words_flagged_by_analysis_are_required_too(settings: Settings) -> None:
    claim = "Senator Jane Doe described the ban as going much too far."
    resp = await _verify(
        settings,
        claim,
        [_src("S1", "wire.example.net", 'Doe said: "The ban is far too strict for families."')],
        "supported",
        {"S1": "supports"},
        analysis=_analysis(
            claim, attributed_to="Senator Jane Doe", exact_words=["the ban goes much too far"]
        ),
    )
    assert resp.verdict == "insufficient_evidence"
    assert _rules(resp) == ["exact_quote_not_confirmed"]


# ------------------------------------------------------------- detection and non-regression


async def test_attribution_flagged_by_analysis_without_a_speech_verb(settings: Settings) -> None:
    claim = "According to Senator Jane Doe, she will keep the ban."
    resp = await _verify(
        settings,
        claim,
        [_src("S1", "news.example.com", CHARACTERIZATION)],
        "supported",
        {"S1": "supports"},
        analysis=_analysis(claim, attributed_to="Senator Jane Doe"),
    )
    assert resp.verdict == "insufficient_evidence"


async def test_russian_reported_speech_is_detected(settings: Settings) -> None:
    resp = await _verify(
        settings,
        "Сенатор Доу заявила, что проголосует за сохранение запрета.",
        [_src("S1", "news.example.ru", "Издание считает, что Доу фактически поддерживает запрет.")],
        "supported",
        {"S1": "supports"},
    )
    assert resp.verdict == "insufficient_evidence"
    assert _rules(resp) == ["attribution_without_direct_evidence"]


async def test_plain_factual_claims_are_unaffected(settings: Settings) -> None:
    resp = await _verify(
        settings,
        FACTUAL,
        [_src("S1", "records.example.gov", NEWS_FACT)],
        "supported",
        {"S1": "supports"},
    )
    assert resp.verdict == "supported" and resp.verdict_adjustments == []


async def test_mixed_and_uncertain_verdicts_are_left_alone(settings: Settings) -> None:
    resp = await _verify(
        settings,
        SAID_KEEP,
        [_src("S1", "news.example.com", CHARACTERIZATION)],
        "mixed",
        {"S1": "partially_supports"},
    )
    assert resp.verdict == "mixed" and resp.verdict_adjustments == []


# ------------------------------------------------------------- warnings versus safeguards


async def test_a_warning_alone_is_not_recorded_as_a_safeguard(settings: Settings) -> None:
    # Quoted words not on the page: a warning; the verdict stands on a real transcript line.
    resp = await _verify(
        settings,
        SAID_KEEP,
        [_src("S1", "transcripts.example.org", TRANSCRIPT)],
        "supported",
        {"S1": "supports"},
        'She promised to "never touch the ban again" [S1].',
    )
    assert resp.verdict == "supported"
    assert resp.verdict_adjustments == []
    assert any("never touch the ban again" in n for n in resp.guardrail_notes)


async def test_existing_no_backing_source_rule_is_recorded_as_a_safeguard(
    settings: Settings,
) -> None:
    resp = await _verify(
        settings,
        FACTUAL,
        [_src("S1", "records.example.gov", NEWS_FACT)],
        "supported",
        {"S1": "irrelevant"},
    )
    assert resp.verdict == "insufficient_evidence"
    assert _rules(resp) == ["verdict_without_backing_source"]
    assert any("Verdict changed from 'supported'" in n for n in resp.guardrail_notes)  # kept


def test_reported_speech_detection_and_known_false_alarms() -> None:
    from app.services.attribution import detect_attribution

    def is_attr(claim: str) -> bool:
        return detect_attribution(claim, "", []).is_attribution

    assert is_attr("Saying he would vote no amounts to saying he backs the ban.")
    assert is_attr("Doe told reporters she would vote no.")
    assert is_attr("Доу сказала, что проголосует против.")
    assert not is_attr("The United States has more than 330 million residents.")
    assert not is_attr("Jobless claims rose 5% in March, according to the Labor Department.")
    assert not is_attr("Several states banned the practice in 2023.")
    # Quoted words make it an exact-quote claim even without a speech verb.
    assert detect_attribution('Her motto: "never give up on them"', "", []).exact_words


# ----------------------------------------------------------- whose words: the speaker check

MODERATOR_LINE = (
    "DAVIS: The very next day you reversed course and said you would vote to support the "
    "six-week ban."
)
OTHER_SPEAKER_QUOTE = 'Roe told reporters: "She will vote to keep the ban in place."'


async def test_a_moderator_quoting_the_claim_is_not_the_speakers_own_words(
    settings: Settings,
) -> None:
    resp = await _verify(
        settings,
        "Donald Trump said he would vote to support the six-week ban.",
        [_src("S1", "transcripts.example.org", MODERATOR_LINE)],
        "supported",
        {"S1": "supports"},
    )
    assert resp.verdict == "insufficient_evidence"
    assert _rules(resp) == ["attribution_without_direct_evidence"]


async def test_a_quote_from_someone_else_is_not_the_speakers_own_words(settings: Settings) -> None:
    resp = await _verify(
        settings,
        SAID_KEEP,
        [_src("S1", "wire.example.net", OTHER_SPEAKER_QUOTE)],
        "supported",
        {"S1": "supports"},
    )
    assert resp.verdict == "insufficient_evidence"


async def test_speaker_from_analysis_is_used_when_the_claim_has_no_named_subject(
    settings: Settings,
) -> None:
    claim = "Saying no to the measure amounts to saying she backs the ban."
    resp = await _verify(
        settings,
        claim,
        [_src("S1", "transcripts.example.org", "ROE: I back the ban completely.")],
        "supported",
        {"S1": "supports"},
        analysis=_analysis(claim, attributed_to="Senator Jane Doe"),
    )
    assert resp.verdict == "insufficient_evidence"  # Roe's words, not Doe's


def test_speaker_names_are_taken_from_the_claim() -> None:
    from app.services.attribution import detect_attribution

    assert detect_attribution(SAID_KEEP, "", []).speaker_tokens == frozenset({"jane", "doe"})
    assert (
        "trump"
        in detect_attribution("In late August 2024 Trump then said X.", "", []).speaker_tokens
    )
    assert (
        detect_attribution("Saying no amounts to saying yes.", "", []).speaker_tokens == frozenset()
    )
