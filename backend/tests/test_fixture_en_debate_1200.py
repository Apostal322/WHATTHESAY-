"""Offline evidence fixture and proposition-level reasoning for case en-debate-1200.

Two kinds of data appear here, and they are kept apart:

- The COMMITTED fixture (eval/fixtures/en-debate-1200/fixture.json): real URLs and metadata.
  Its sources are `pending_capture` until scripts/capture_evidence.py runs on a networked
  machine; these tests check that it never pretends otherwise, and every captured excerpt is
  checked against the excerpt policy as soon as one exists.
- SYNTHETIC pages built in this file (on example.org hosts, text marked SYNTHETIC). They are
  NOT the real sources' wording; they exercise the real capture, retrieval, guardrail and
  scoring code with case-shaped inputs (a transcript line, a headline that characterizes,
  a wire report, an official-record sentence).

The model is scripted per call. What is tested is the deterministic business logic around
it: what the model may cite, when quoted words are flagged, how verdicts are downgraded,
chronology, and per-proposition scoring. No test here measures factual accuracy.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.core.errors import RetrievalUnavailableError
from app.evaluation.chronology import check_temporal, on_record_by
from app.evaluation.fixture import (
    MAX_EXCERPTS_PER_SOURCE,
    EvidenceFixture,
    FixtureRetriever,
    capture_from_paragraphs,
    page_hash,
)
from app.evaluation.propositions import verify_propositions
from app.evaluation.reference import ReferenceRecord
from app.models.schemas import VerifyRequest
from app.services.citations import EvidenceSource
from app.services.evidence import MAX_PASSAGE_CHARS
from app.services.llm import LLMResult, Usage, VideoClip
from app.services.verification import VerificationService, check_quotations

REPO = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO / "eval" / "fixtures" / "en-debate-1200" / "fixture.json"
REF_PATH = REPO / "eval" / "reference" / "en-debate-1200.json"


def _fixture() -> EvidenceFixture:
    return EvidenceFixture.model_validate(json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))


def _ref() -> ReferenceRecord:
    return ReferenceRecord.model_validate(json.loads(REF_PATH.read_text(encoding="utf-8")))


# ------------------------------------------------------------- the committed fixture


def test_committed_fixture_is_labelled_offline_and_never_live() -> None:
    fx = _fixture()
    assert fx.kind == "offline_regression_fixture" and fx.live_retrieval is False
    assert "NOT live retrieval" in fx.description
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    raw["live_retrieval"] = True
    with pytest.raises(ValidationError):
        EvidenceFixture.model_validate(raw)


def test_fixture_sources_keep_the_reference_urls_dates_and_types() -> None:
    fx, ref = _fixture(), _ref()
    ref_ev = {e.id: e for e in ref.evidence}
    props = {p.id for p in ref.propositions}
    for src in fx.sources:
        ev = ref_ev[src.id]
        assert (src.url, src.publisher, src.source_type) == (ev.url, ev.publisher, ev.source_type)
        assert src.published == ev.published
        assert src.located_by == ev.located_by
        assert set(src.relevant_to) <= props
    # the newly located transcript and official record are in, with their original URLs
    assert fx.source("U5").url.startswith("https://rollcall.com/factbase/trump/transcript/")
    assert "account=83927&seqnum=1" in fx.source("U3").url
    assert fx.source("U3").source_type == "official_record"


def test_uncaptured_sources_carry_no_text_and_log_their_attempts() -> None:
    fx = _fixture()
    for src in fx.sources:
        if src.status == "pending_capture":
            assert src.capture is None
    blocked = [s for s in fx.sources if s.attempts]
    assert {s.id for s in blocked} >= {"U2", "U3", "U5"}  # tried from the cloud sandbox
    assert all("sandbox" in a.environment for s in blocked for a in s.attempts)


def test_every_captured_excerpt_satisfies_the_excerpt_policy() -> None:
    """Vacuous until a capture is committed; then it guards every excerpt."""
    for src in _fixture().captured():
        assert src.capture is not None
        cap = src.capture
        assert len(cap.excerpts) <= MAX_EXCERPTS_PER_SOURCE
        assert len(cap.page_sha256) == 64 and cap.paragraph_count > 0
        assert cap.method in ("app_fetch_page", "user_saved_html")
        for ex in cap.excerpts:
            assert len(ex.text) <= MAX_PASSAGE_CHARS
            assert ex.anchor.lower() in " ".join(ex.text.lower().split())
            assert ex.anchor not in cap.missing_anchors


async def test_retriever_refuses_an_uncaptured_fixture(settings: Settings) -> None:
    fx = _fixture().model_copy(deep=True)
    for s in fx.sources:
        s.capture = None
    with pytest.raises(RetrievalUnavailableError) as err:
        await FixtureRetriever(fx).retrieve(claim="c", queries=[], scope_notes=None, language="en")
    assert err.value.diagnostics and "U5" in err.value.diagnostics["pending_capture"]


# ------------------------------------------------------------ capture (synthetic page)

SYN_TRANSCRIPT = [
    "SYNTHETIC TEST PAGE - not the real transcript.",
    "MODERATOR (00:15:55): You initially said six weeks was too short, but then the very next "
    "day you reversed course on the six-week question. SYNTHETIC.",
    "CANDIDATE (00:16:20): SYNTHETIC reply text for tests.",
]


def test_capture_keeps_verbatim_windows_and_reports_missing_anchors() -> None:
    now = datetime(2026, 10, 14, tzinfo=UTC)
    cap = capture_from_paragraphs(
        SYN_TRANSCRIPT,
        ["very next day", "six-week", "not on the page"],
        final_url="https://transcript.example.org/debate",
        method="app_fetch_page",
        environment="test",
        now=now,
    )
    assert [e.text for e in cap.excerpts] == [SYN_TRANSCRIPT[1]]  # one window, not duplicated
    assert cap.missing_anchors == ["not on the page"]
    assert cap.page_sha256 == page_hash(SYN_TRANSCRIPT)
    assert cap.captured_at == "2026-10-14T00:00:00+00:00"


def test_capture_never_returns_text_that_is_not_on_the_page() -> None:
    cap = capture_from_paragraphs(
        ["SYNTHETIC page about something else entirely, with enough words to window."],
        ["six-week"],
        final_url="https://x.example.org/",
        method="user_saved_html",
        environment="test",
    )
    assert cap.excerpts == [] and cap.missing_anchors == ["six-week"]


def test_long_paragraphs_are_cut_to_policy_length() -> None:
    para = (
        ("SYNTHETIC filler sentence number one. " * 20)
        + "The anchor phrase is here. "
        + ("More SYNTHETIC filler follows. " * 20)
    )
    cap = capture_from_paragraphs(
        [para],
        ["anchor phrase"],
        final_url="https://x.example.org/",
        method="app_fetch_page",
        environment="test",
    )
    assert len(cap.excerpts) == 1 and len(cap.excerpts[0].text) <= MAX_PASSAGE_CHARS
    assert cap.excerpts[0].text in para


# --------------------------------------------- a synthetic captured fixture for the pipeline

# Case-shaped SYNTHETIC texts. They mimic the *kinds* of source involved, not their wording.
T_TRANSCRIPT = (
    # Speaker labelled by name, as in the Factbase transcripts: an anonymous label would not
    # show whose words these are (see the attribution speaker check).
    "SYNTHETIC transcript: DONALD TRUMP: I will be voting no, because it goes too far. SYNTHETIC."
)
T_NEWS = (
    "SYNTHETIC headline style: candidate effectively votes to uphold the six-week ban, outlet says."
)
T_WIRE = "SYNTHETIC wire: he said on Friday he would vote against the amendment, a day after calling six weeks too short."
T_OFFICIAL = "SYNTHETIC official record: no law shall restrict the procedure before viability, under this test text."


def _synthetic_fixture() -> EvidenceFixture:
    def src(sid: str, url: str, kind: str, text: str, anchor: str) -> dict[str, Any]:
        cap = capture_from_paragraphs(
            [text],
            [anchor],
            final_url=url,
            method="app_fetch_page",
            environment="pytest synthetic",
            now=datetime(2026, 10, 14, tzinfo=UTC),
        )
        return {
            "id": sid,
            "url": url,
            "publisher": f"SYNTHETIC {sid}",
            "source_type": kind,
            "published": "2024-08-30",
            "located_by": "user",
            "relevant_to": ["P1"],
            "anchors": [anchor],
            "capture": cap.model_dump(),
        }

    return EvidenceFixture.model_validate(
        {
            "kind": "offline_regression_fixture",
            "live_retrieval": False,
            "case_id": "synthetic",
            "description": "SYNTHETIC",
            "excerpt_policy": "test",
            "sources": [
                src(
                    "U4",
                    "https://transcript.example.org/fox",
                    "primary_transcript",
                    T_TRANSCRIPT,
                    "voting no",
                ),
                src("R5", "https://news.example.com/cbs", "news_report", T_NEWS, "uphold"),
                src(
                    "U2", "https://wire.example.net/reuters", "wire_report", T_WIRE, "vote against"
                ),
                src(
                    "U3",
                    "https://records.example.gov/amendment",
                    "official_record",
                    T_OFFICIAL,
                    "viability",
                ),
            ],
        }
    )


class ScriptedLLM:
    """Returns the next scripted response for each schema, in call order."""

    def __init__(self, script: dict[str, list[dict[str, Any]]]) -> None:
        self.script = {k: list(v) for k, v in script.items()}
        self.calls: list[tuple[str, str]] = []

    async def generate_json(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        schema: type[BaseModel],
        video: VideoClip | None = None,
    ) -> LLMResult:
        self.calls.append((schema.__name__, prompt))
        return LLMResult(
            text=json.dumps(self.script[schema.__name__].pop(0)),
            usage=Usage(prompt_tokens=10, output_tokens=5, total_tokens=15),
            model=model,
        )

    async def grounded_search(self, **kw: Any) -> Any:  # pragma: no cover - must not happen
        raise AssertionError("offline fixture runs must not search")


def _analysis(claim: str) -> dict[str, Any]:
    return {
        "normalized_claim": claim,
        "claim_type": "factual",
        "is_checkworthy": True,
        "reason": "r",
        "search_queries": ["q"],
        "scope_notes": "As of 10 September 2024.",
    }


def _evaluation(
    verdict: str,
    stances: dict[str, str],
    explanation: str,
    findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "source_assessments": [
            {"source_id": k, "stance": v, "note": "n"} for k, v in stances.items()
        ],
        "verdict": verdict,
        "explanation": explanation,
        "key_findings": findings or [],
        "limitations": [],
    }


async def test_fixture_evidence_is_labelled_in_every_response(settings: Settings) -> None:
    llm = ScriptedLLM(
        {
            "ClaimAnalysisOutput": [_analysis("He said he would vote no.")],
            "EvaluationOutput": [
                _evaluation("supported", {"S1": "supports"}, "He said he would vote no [S1].")
            ],
        }
    )
    svc = VerificationService(llm, settings, retriever=FixtureRetriever(_synthetic_fixture()))
    resp = await svc.verify(VerifyRequest(claim="He said he would vote no.", language="en"))
    assert resp.search_queries == []  # nothing was searched
    assert any("offline regression fixture" in n for n in resp.guardrail_notes)
    assert all(s.excerpt_origin == "page_text" for s in resp.sources)
    assert [c for c, _ in llm.calls] == ["ClaimAnalysisOutput", "EvaluationOutput"]


# ----------------------------------------------- direct quotation vs. author's interpretation


def _src(sid: str, text: str, origin: str = "page_text") -> EvidenceSource:
    return EvidenceSource(
        sid,
        f"https://{sid.lower()}.example.org/",
        None,
        f"{sid.lower()}.example.org",
        [text],
        origin=origin,
    )  # type: ignore[arg-type]


def test_a_quotation_found_verbatim_in_page_text_passes() -> None:
    notes = check_quotations("He said “I will be voting no” [S1].", [_src("S1", T_TRANSCRIPT)])
    assert notes == []


def test_a_characterization_presented_as_a_quotation_is_flagged() -> None:
    notes = check_quotations(
        'Trump said he would "vote to support the six-week ban" [S2].', [_src("S2", T_NEWS)]
    )
    assert len(notes) == 1 and "not found verbatim" in notes[0]
    assert "interpretation" in notes[0]


def test_quoted_words_only_in_a_snippet_are_not_treated_as_verified() -> None:
    notes = check_quotations(
        'He said "I will be voting no".', [_src("S3", T_TRANSCRIPT, origin="search_snippet")]
    )
    assert len(notes) == 1 and "search snippet or model summary" in notes[0]


def test_short_quotes_and_apostrophes_are_ignored() -> None:
    assert check_quotations('The "ban" was in force; it\'s his "no".', [_src("S1", T_NEWS)]) == []


async def test_pipeline_flags_quoted_characterization_but_keeps_the_verdict_logic(
    settings: Settings,
) -> None:
    """The quote check is advisory; verdict guardrails decide the verdict independently."""
    claim = "Trump said he would vote to support the six-week ban."
    llm = ScriptedLLM(
        {
            "ClaimAnalysisOutput": [_analysis(claim)],
            "EvaluationOutput": [
                _evaluation(
                    "supported",
                    {"S1": "supports", "S2": "partially_supports"},
                    'He said "I will be voting no" [S1] and would "vote to support the six-week ban" [S2].',
                    [
                        {
                            "text": 'Trump said he would "vote to support the six-week ban".',
                            "stance": "supports",
                            "source_ids": ["S2"],
                        }
                    ],
                )
            ],
        }
    )
    fx = _synthetic_fixture()
    svc = VerificationService(llm, settings, retriever=FixtureRetriever(fx, ["U4", "R5"]))
    resp = await svc.verify(VerifyRequest(claim=claim, language="en"))
    flagged = [n for n in resp.guardrail_notes if "vote to support the six-week ban" in n]
    assert len(flagged) == 1  # explanation + finding: the same quote is reported once
    assert not any("I will be voting no" in n for n in resp.guardrail_notes)


# --------------------------------------------------------------------- chronology


def test_very_next_day_is_consistent_but_unconfirmed_on_reported_dates() -> None:
    ref = _ref()
    c = check_temporal(ref, ref.proposition("P3"))
    assert (c["days"], c["status"]) == (1, "consistent_unconfirmed")
    assert any("recording date" in x for x in c["caveats"])


def test_chronology_detects_a_gap_that_is_not_the_next_day() -> None:
    raw = json.loads(REF_PATH.read_text(encoding="utf-8"))
    next(e for e in raw["events"] if e["id"] == "E1")["date"] = "2024-08-27"
    ref = ReferenceRecord.model_validate(raw)
    assert check_temporal(ref, ref.proposition("P3"))["status"] == "inconsistent"


def test_chronology_is_firm_only_on_primary_confirmed_dates() -> None:
    raw = json.loads(REF_PATH.read_text(encoding="utf-8"))
    for e in raw["events"]:
        e["date_basis"] = "confirmed_primary"
    ref = ReferenceRecord.model_validate(raw)
    assert check_temporal(ref, ref.proposition("P3"))["status"] == "consistent"


def test_later_events_are_separated_from_what_was_on_record() -> None:
    raw = json.loads(REF_PATH.read_text(encoding="utf-8"))
    raw["events"].append(
        {
            "id": "E4",
            "description": "Amendment 4 fails at the ballot.",
            "date": "2024-11-05",
            "date_basis": "reported",
        }
    )
    split = on_record_by(ReferenceRecord.model_validate(raw))
    assert [e.id for e in split["on_record"]] == ["E1", "E2", "E3"]
    assert [e.id for e in split["after_claim"]] == ["E4"]


# ------------------------------------------------------------ proposition by proposition


async def test_each_proposition_is_verified_and_scored_on_its_own(settings: Settings) -> None:
    """Scripted evaluator; the guardrails, chronology and per-proposition scoring are real."""
    ref = _ref()
    S = {"S1": "supports", "S2": "irrelevant", "S3": "supports", "S4": "irrelevant"}
    script = {
        "ClaimAnalysisOutput": [_analysis(p.text) for p in ref.propositions],
        "EvaluationOutput": [
            # P1 six-week position: the evaluator leans on the wire report, which only
            # paraphrases him ("a day after calling six weeks too short").
            _evaluation("supported", {**S, "S1": "irrelevant"}, "Called six weeks too short [S3]."),
            # P2 vote on Amendment 4: the transcript backs it.
            _evaluation("supported", S, "He said “I will be voting no” [S1]."),
            # P3 chronology: a careful evaluator flags the reported dates.
            _evaluation(
                "mixed", {**S, "S3": "partially_supports"}, "A day apart as reported [S3]."
            ),
            # P4 characterization: an evaluator that takes the headline's framing as his words.
            _evaluation(
                "supported",
                {**S, "S2": "supports"},
                'He said he would "vote to support the six-week ban" [S2].',
            ),
            # P5 legal effect: a confident 'contradicted' with no contradicting source.
            _evaluation("contradicted", {**S, "S4": "supports"}, "The record says otherwise."),
        ],
    }
    svc = VerificationService(
        ScriptedLLM(script), settings, retriever=FixtureRetriever(_synthetic_fixture())
    )
    results = {r.proposition_id: r for r in await verify_propositions(svc, ref)}

    # P1 quotes his wording ('voting that we need more than six weeks'); the wire report only
    # paraphrases him -> no confident verdict (a miss, not a confident error).
    p1 = results["P1"]
    assert p1.response and p1.response.verdict == "insufficient_evidence"
    assert [a.rule for a in p1.response.verdict_adjustments] == ["exact_quote_not_confirmed"]
    assert not p1.score["false_confident"]
    # P2: the transcript line carries his own words ("DONALD TRUMP: I will be voting no").
    p2 = results["P2"]
    assert p2.score["acceptable"] and p2.response and p2.response.verdict_adjustments == []
    assert results["P3"].response and results["P3"].response.verdict == "mixed"
    assert results["P3"].score["acceptable"]
    assert (
        results["P3"].chronology and results["P3"].chronology["status"] == "consistent_unconfirmed"
    )

    # P4: the headline's framing ('would vote to support the six-week ban') taken for his words.
    # Before the attribution safeguard this produced a confident 'supported', a false
    # confident verdict against the reference, even though his actual words in the transcript
    # ("I will be voting no") do not contain that wording.
    p4 = results["P4"]
    assert p4.response and p4.response.verdict == "insufficient_evidence"
    assert [a.rule for a in p4.response.verdict_adjustments] == ["exact_quote_not_confirmed"]
    assert not p4.score["false_confident"]
    assert any("vote to support the six-week ban" in n for n in p4.response.guardrail_notes)

    p5 = results["P5"]
    assert p5.response and p5.response.verdict == "insufficient_evidence"  # downgraded
    assert not p5.score["false_confident"]

    assert all(r.score["counted"] is False for r in results.values())  # AI-reviewed key


async def test_propositions_report_no_verdict_when_nothing_was_captured(settings: Settings) -> None:
    ref = _ref()
    llm = ScriptedLLM({"ClaimAnalysisOutput": [_analysis("x")], "EvaluationOutput": []})
    fx = _fixture().model_copy(deep=True)
    for s in fx.sources:
        s.capture = None
    svc = VerificationService(llm, settings, retriever=FixtureRetriever(fx))
    [r] = await verify_propositions(svc, ref, only=["P2"])
    assert r.error == "retrieval_unavailable" and r.score["outcome"] == "no_verdict"


def test_capture_script_parses_a_saved_page_into_the_fixture(tmp_path: Path) -> None:
    """scripts/capture_evidence.py --html-file: the app parses a browser-saved page (SYNTHETIC)."""
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[2]
    fx = tmp_path / "fixture.json"
    fx.write_text(
        (repo / "eval" / "fixtures" / "en-debate-1200" / "fixture.json").read_text("utf-8"),
        encoding="utf-8",
    )
    page = tmp_path / "saved.html"
    page.write_text(
        "<p>SYNTHETIC test page: limits before viability are the subject here.</p>",
        encoding="utf-8",
    )
    script = repo / "scripts" / "capture_evidence.py"
    args = ["--fixture", str(fx), "--source", "U3", "--html-file", str(page)]
    done = subprocess.run(
        [sys.executable, str(script), *args, "--environment", "pytest"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    data = json.loads(fx.read_text("utf-8"))
    cap = next(s for s in data["sources"] if s["id"] == "U3")["capture"]
    assert cap["method"] == "user_saved_html" and cap["environment"] == "pytest"
    assert cap["excerpts"][0]["text"].startswith("SYNTHETIC")
    assert all(s["capture"] is None for s in data["sources"] if s["id"] != "U3")


def test_quoting_the_claim_under_review_gets_an_accurate_note() -> None:
    claim = "AI has already replaced 80% of software engineers."
    notes = check_quotations(
        'The claim that "AI has already replaced 80% of software engineers" is not supported [S1].',
        [_src("S1", T_NEWS)],
        (claim,),
    )
    assert len(notes) == 1
    assert "wording of the claim being checked" in notes[0]
    assert "not a direct quotation of the speaker" in notes[0]
    assert "paraphrase or an interpretation" not in notes[0]


async def test_redirected_capture_is_credited_to_the_site_actually_read() -> None:
    from app.evaluation.fixture import FixtureRetriever

    cap = capture_from_paragraphs(
        [T_WIRE],
        ["vote against"],
        final_url="https://www.wire-mirror.example.com/story",
        method="app_fetch_page",
        environment="pytest synthetic",
        now=datetime(2026, 10, 14, tzinfo=UTC),
    )
    fx = EvidenceFixture.model_validate(
        {
            "kind": "offline_regression_fixture",
            "live_retrieval": False,
            "case_id": "synthetic",
            "description": "SYNTHETIC",
            "excerpt_policy": "test",
            "sources": [
                {
                    "id": "U2",
                    "url": "https://short.example.net/r",
                    "publisher": "SYNTHETIC",
                    "source_type": "wire_report",
                    "located_by": "user",
                    "relevant_to": ["P2"],
                    "anchors": ["vote against"],
                    "capture": cap.model_dump(),
                }
            ],
        }
    )
    res = await FixtureRetriever(fx).retrieve(
        claim="c", queries=[], scope_notes=None, language="en"
    )
    assert res.sources[0].url == "https://www.wire-mirror.example.com/story"
    assert res.sources[0].domain == "wire-mirror.example.com"
