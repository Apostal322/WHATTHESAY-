"""Steps C-F: analyse claim -> grounded retrieval -> evidence evaluation -> guardrails."""

from __future__ import annotations

import dataclasses
import hashlib
import logging
import re
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Literal

import httpx

from app.core.cache import TTLCache
from app.core.config import Settings
from app.core.errors import AppError
from app.core.singleflight import SingleFlight
from app.models.llm_outputs import ClaimAnalysisOutput, EvaluationOutput
from app.models.schemas import (
    ClaimType,
    KeyFinding,
    SourceRef,
    Stance,
    Verdict,
    VerdictAdjustment,
    VerifyRequest,
    VerifyResponse,
)
from app.services import prompts
from app.services.attribution import (
    apply_entailment,
    check_attribution_evidence,
    detect_attribution,
)
from app.services.citations import EvidenceSource, format_sources_for_prompt
from app.services.entailment import EntailmentChecker, ModelEntailmentChecker
from app.services.evidence import origin_groups
from app.services.llm import LLMClient, Usage
from app.services.retrieval import GeminiGroundingRetriever, RetrievalResult, Retriever
from app.services.structured import clip, parse_model_json, to_token_usage
from app.services.temporal import temporal_context

log = logging.getLogger(__name__)

Stage = Literal["analyzing", "searching", "evaluating"]
ProgressCallback = Callable[[Stage], Awaitable[None]]

NON_CHECKABLE: set[ClaimType] = {"opinion", "prediction", "non_verifiable"}
# "Nothing found" may be a passing gap (an index lag, a short outage upstream): re-check soon.
NO_EVIDENCE_CACHE_TTL_SEC = 300
# Bump when prompts or verdict safeguards change what a cached response would say.
VERIFICATION_POLICY_VERSION = "2026-10-19.4"
_CITATION_RE = re.compile(r"\[(S\d{1,2})\]")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([.,;:!?])")
# Text between straight or curly double quotes; single quotes are too often apostrophes.
_DQ = '"\u201c\u201d\u201e'
_QUOTED_RE = re.compile(f"[{_DQ}]([^{_DQ}]{{3,300}})[{_DQ}]")
_QUOTE_CHARS = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'})
_MIN_QUOTED_WORDS = 3


async def _noop(_: Stage) -> None:
    return None


# ------------------------------------------------------------------ guardrails


def apply_verdict_guardrails(
    verdict: Verdict, stances: dict[str, Stance], sources: dict[str, EvidenceSource]
) -> tuple[Verdict, list[str]]:
    """Enforce that a verdict is backed by the evaluator's own per-source stances.

    `stances` must already be filtered to known source ids. Returns the final
    verdict and human-readable notes describing any change.
    """
    notes: list[str] = []
    supports = {sid for sid, st in stances.items() if st == "supports"}
    contradicts = {sid for sid, st in stances.items() if st == "contradicts"}
    partial = {sid for sid, st in stances.items() if st == "partially_supports"}

    final = verdict
    if (
        (verdict == "supported" and not supports)
        or (verdict == "contradicted" and not contradicts)
        or (verdict == "mixed" and not (partial or (supports and contradicts)))
    ):
        final = "insufficient_evidence"
    if final != verdict:
        notes.append(
            f"Verdict changed from '{verdict}' to '{final}': no retrieved source was assessed "
            "as backing it."
        )

    basis = supports if final == "supported" else contradicts if final == "contradicted" else set()
    if basis:
        groups = origin_groups(list(sources.values()))
        if len({groups[sid] for sid in basis}) == 1:
            notes.append(
                "The verdict rests on a single source domain or original report; treat it "
                "with caution."
            )
    if final == "supported" and contradicts:
        notes.append("Some retrieved sources contradict the claim; see the evidence list.")
    if final == "contradicted" and supports:
        notes.append("Some retrieved sources support the claim; see the evidence list.")
    return final, notes


def strip_unknown_citations(text: str, known: set[str]) -> str:
    stripped = _CITATION_RE.sub(lambda m: m.group(0) if m.group(1) in known else "", text)
    return _SPACE_BEFORE_PUNCT_RE.sub(r"\1", re.sub(r" {2,}", " ", stripped)).strip()


def _fold_quote(text: str) -> str:
    text = " ".join(text.translate(_QUOTE_CHARS).lower().split())
    return text.strip(" .,;:!?'\"")


def check_quotations(
    text: str, cited: list[EvidenceSource], claim_texts: tuple[str, ...] = ()
) -> list[str]:
    """Quoted words in model text must occur verbatim in the page text of the cited sources.

    Model explanations often put an outlet's paraphrase, a headline's framing or the claim's
    own wording in quotation marks, which reads as a direct quotation of the person. This
    check does not judge meaning; it only says when quoted words are not backed verbatim, so
    an author's interpretation is not mistaken for what was said.
    """
    notes: list[str] = []
    claims = [_fold_quote(c) for c in claim_texts if c]
    for match in _QUOTED_RE.finditer(text):
        quoted = _fold_quote(match.group(1))
        if len(quoted.split()) < _MIN_QUOTED_WORDS:
            continue
        in_page = [
            s.id
            for s in cited
            if s.origin == "page_text" and any(quoted in _fold_quote(e) for e in s.excerpts)
        ]
        if in_page:
            continue
        elsewhere = [s.id for s in cited if any(quoted in _fold_quote(e) for e in s.excerpts)]
        shown = clip(match.group(1).strip(), 80)
        if elsewhere:
            notes.append(
                f"Quoted words \u201c{shown}\u201d appear only in a search snippet or model "
                f"summary ({', '.join(elsewhere)}), not in verbatim page text."
            )
        elif any(quoted in c for c in claims):
            # Quoting the claim under review is normal; say so instead of calling it a
            # paraphrase, but still make clear it is not a quotation of the person.
            notes.append(
                f"Quoted words \u201c{shown}\u201d are the wording of the claim being checked; "
                "the cited sources do not contain them verbatim, so they are not a direct "
                "quotation of the speaker."
            )
        else:
            notes.append(
                f"Quoted words \u201c{shown}\u201d were not found verbatim in the cited "
                "source excerpts: treat them as a paraphrase or an interpretation, not a "
                "direct quotation."
            )
    return notes


# Source stances that can back a key finding with the given stance.
_COMPATIBLE: dict[Stance, set[Stance]] = {
    "supports": {"supports", "partially_supports"},
    "contradicts": {"contradicts", "partially_supports"},
    "partially_supports": {"supports", "partially_supports", "contradicts"},
    "unclear": set(),
    "irrelevant": set(),
}


def _same_origin(sources: list[EvidenceSource]) -> dict[str, list[str]]:
    groups = origin_groups(sources)
    return {
        s.id: [o.id for o in sources if o.id != s.id and groups[o.id] == groups[s.id]]
        for s in sources
    }


def independent_domains(stances: dict[str, Stance], sources: dict[str, EvidenceSource]) -> int:
    """Independent origins among sources that bear on the claim (support, contradict or partly
    support it); sites and republished copies count once. `unclear` sources do not count."""
    used = {
        sid
        for sid, st in stances.items()
        if st in ("supports", "contradicts", "partially_supports")
    }
    groups = origin_groups(list(sources.values()))
    return len({groups[sid] for sid in used})


def verification_cache_key(req: VerifyRequest, model: str, retriever: str) -> str:
    """Everything the verdict depends on: the claim *and* its context.

    The same words are a different claim in a different video ("He said he would vote no"),
    and the context is given to both the analysis and the evaluation prompts.
    """
    parts = [
        " ".join(req.claim.lower().split()),
        " ".join((req.context or "").split()),
        # When it was said, as reconciled (conflicting or impossible dates are dropped).
        temporal_context(req.statement_date, req.video_published_date).cache_part(),
        req.language,
        model,
        retriever,
        VERIFICATION_POLICY_VERSION,
    ]
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


# --------------------------------------------------------------------- service


class VerificationService:
    def __init__(
        self,
        llm: LLMClient,
        settings: Settings,
        http_client: httpx.AsyncClient | None = None,
        cache: TTLCache[VerifyResponse] | None = None,
        retriever: Retriever | None = None,
        flights: SingleFlight[VerifyResponse, Stage] | None = None,
        entailment: EntailmentChecker | None = None,
    ) -> None:
        self._llm = llm
        self._entailment = entailment or ModelEntailmentChecker(llm, settings.verification_model)
        self._settings = settings
        self._cache = cache
        self._flights = flights
        self._retriever = retriever or GeminiGroundingRetriever(
            llm, settings.verification_model, http_client, settings.resolve_source_redirects
        )

    async def verify(
        self, req: VerifyRequest, progress: ProgressCallback = _noop
    ) -> VerifyResponse:
        s = self._settings
        model = s.verification_model
        cache_key = verification_cache_key(
            req, model, f"{self._retriever.name}+{self._entailment.name}"
        )
        if self._cache is not None and (hit := self._cache.get(cache_key)) is not None:
            log.info("verify_cache_hit", extra={"verdict": hit.verdict})
            return hit.model_copy(update={"cached": True, "latency_ms": 0})
        if self._flights is None:
            return await self._verify(req, cache_key, progress)
        # Identical concurrent requests share one verification (and its model calls).
        response, joined = await self._flights.run(
            cache_key, lambda emit: self._verify(req, cache_key, emit), listener=progress
        )
        if joined:
            log.info("verify_coalesced", extra={"verdict": response.verdict})
        return response.model_copy(update={"cached": True}) if joined else response

    async def _verify(
        self, req: VerifyRequest, cache_key: str, progress: ProgressCallback
    ) -> VerifyResponse:
        s = self._settings
        model = s.verification_model
        started = time.perf_counter()
        stage_ms: dict[str, int] = {}  # which stage was slow, in the log line

        def lap(stage: str, since: float) -> float:
            now = time.perf_counter()
            stage_ms[stage] = int((now - since) * 1000)
            return now

        mark = started
        usage = Usage()
        when = temporal_context(req.statement_date, req.video_published_date)

        # --- Step A': classify + plan queries (also covers manual claims) ----
        await progress("analyzing")
        res = await self._llm.generate_json(
            model=model,
            system=prompts.ANALYSIS_SYSTEM,
            prompt=prompts.analysis_prompt(
                claim=req.claim,
                context=req.context,
                language=req.language,
                temporal=when.prompt_text(),
            ),
            schema=ClaimAnalysisOutput,
        )
        usage += res.usage
        models_used = {"analysis": res.model}
        analysis = parse_model_json(res.text, ClaimAnalysisOutput)
        mark = lap("analysis", mark)
        normalized = clip(analysis.normalized_claim, 1000) or req.claim
        queries = [q for q in (clip(x, 200) for x in analysis.search_queries) if q][:4] or [
            normalized
        ]

        retrieval: RetrievalResult | None = None
        semantic_check_failed = False  # read by finish(): such a result is never cached

        def finish(*, search_queries: list[str], **kw: object) -> VerifyResponse:
            billed = retrieval.billed_searches if retrieval else 0
            search_cost = retrieval.search_cost_usd if retrieval else None
            resp = VerifyResponse(
                models_used=dict(models_used),
                claim=req.claim,
                normalized_claim=normalized,
                claim_type=analysis.claim_type,
                model=model,
                latency_ms=int((time.perf_counter() - started) * 1000),
                usage=to_token_usage(usage, s, billed, search_cost),
                search_queries=search_queries,
                **kw,
            )
            log.info(
                "claim_verified",
                extra={
                    "verdict": resp.verdict,
                    "claim_type": resp.claim_type,
                    "sources": len(resp.sources),
                    "retrieval": retrieval.backend if retrieval else None,
                    "latency_ms": resp.latency_ms,
                    "stage_ms": dict(stage_ms),
                    "total_tokens": usage.total_tokens,
                    "estimated_cost_usd": resp.usage.estimated_cost_usd,
                    "safeguards": [a.rule for a in resp.verdict_adjustments],
                },
            )
            if self._cache is not None:
                if (retrieval is not None and retrieval.degraded) or semantic_check_failed:
                    pass  # a step failed, so the result is incomplete: never reuse it
                elif retrieval is not None and not resp.sources:
                    self._cache.set(cache_key, resp, ttl_sec=NO_EVIDENCE_CACHE_TTL_SEC)
                else:
                    self._cache.set(cache_key, resp)
            return resp

        if analysis.claim_type in NON_CHECKABLE or not analysis.is_checkworthy:
            return finish(
                verdict="not_verifiable",
                explanation=clip(analysis.reason, 800)
                or "This statement is not an objectively checkable factual claim.",
                key_findings=[],
                sources=[],
                limitations=[
                    "Opinions and predictions are not fact-checked. Edit the claim to isolate a "
                    "factual premise if you want to check one."
                ],
                search_queries=[],
                independent_source_count=0,
                guardrail_notes=[],
            )

        def completed_so_far() -> dict[str, object]:
            """What finished before a failure: kept for review and cost accounting."""
            return {
                "analysis": {
                    "normalized_claim": normalized,
                    "claim_type": analysis.claim_type,
                    "planned_queries": queries,
                    "scope_notes": analysis.scope_notes,
                },
                "retrieval": {
                    "backend": retrieval.backend,
                    "sources": len(retrieval.sources),
                    "executed_queries": retrieval.executed_queries,
                }
                if retrieval
                else None,
                "models_used": dict(models_used),
                "usage": dataclasses.asdict(usage),
            }

        def keep_progress(exc: AppError) -> AppError:
            exc.diagnostics = {**(exc.diagnostics or {}), "completed": completed_so_far()}
            return exc

        # --- Step C: retrieval (grounding or search providers) ----------------
        await progress("searching")
        try:
            retrieval = await self._retriever.retrieve(
                claim=normalized,
                queries=queries,
                scope_notes=analysis.scope_notes,
                language=req.language,
            )
        except AppError as exc:
            raise keep_progress(exc) from None
        usage += retrieval.usage
        mark = lap("retrieval", mark)
        if retrieval.model:
            models_used["retrieval"] = retrieval.model
        retrieved_at = datetime.now(UTC).isoformat(timespec="seconds")
        sources = retrieval.sources
        executed_queries = retrieval.executed_queries

        if not sources:
            return finish(
                verdict="insufficient_evidence",
                explanation=(
                    "The search did not return any citable sources for this claim, so no "
                    "evidence-based verdict can be given."
                ),
                key_findings=[],
                sources=[],
                limitations=[retrieval.empty_reason],
                search_queries=executed_queries,
                independent_source_count=0,
                guardrail_notes=[
                    *retrieval.notices,
                    "No verdict was generated without retrieved evidence.",
                ],
            )

        # --- Step D/E: evaluation ---------------------------------------------
        await progress("evaluating")
        try:
            res = await self._llm.generate_json(
                model=model,
                system=prompts.EVALUATION_SYSTEM,
                prompt=prompts.evaluation_prompt(
                    claim=normalized,
                    scope_notes=analysis.scope_notes,
                    sources_block=format_sources_for_prompt(sources, _same_origin(sources)),
                    context=req.context,
                    temporal=when.prompt_text(),
                    language=req.language,
                ),
                schema=EvaluationOutput,
            )
            usage += res.usage
            models_used["evaluation"] = res.model
            evaluation = parse_model_json(res.text, EvaluationOutput)
            mark = lap("evaluation", mark)
        except AppError as exc:
            raise keep_progress(exc) from None

        # --- Step F: citation integrity + guardrails ---------------------------
        by_id = {src.id: src for src in sources}
        notes: list[str] = [*retrieval.notices, *when.notes]
        stances: dict[str, Stance] = {}
        assessment_notes: dict[str, str | None] = {}
        dropped = 0
        conflicting: set[str] = set()
        for a in evaluation.source_assessments:
            if a.source_id not in by_id:
                dropped += 1
            elif a.source_id in stances and stances[a.source_id] != a.stance:
                # Assessed two ways: neither stance can be relied on to back a verdict.
                conflicting.add(a.source_id)
                stances[a.source_id] = "unclear"
                assessment_notes[a.source_id] = "The evaluation assessed this source both ways."
            elif a.source_id not in conflicting:
                stances[a.source_id] = a.stance
                assessment_notes[a.source_id] = clip(a.note, 400)
        if dropped:
            notes.append(f"Ignored {dropped} assessment(s) that referenced unknown sources.")
        if conflicting:
            notes.append(
                "The evaluation gave conflicting assessments of "
                f"{', '.join(sorted(conflicting))}; treated as unclear."
            )

        # Safeguards (verdict changes) are recorded apart from warnings (guardrail notes).
        adjustments: list[VerdictAdjustment] = []
        verdict, guard_notes = apply_verdict_guardrails(evaluation.verdict, stances, by_id)
        if verdict != evaluation.verdict:
            adjustments.append(
                VerdictAdjustment(
                    rule="verdict_without_backing_source",
                    from_verdict=evaluation.verdict,
                    to_verdict=verdict,
                    reason="No retrieved source was assessed as backing the model's verdict.",
                )
            )
        # Attribution: identity and authenticity (deterministic), then entailment (semantic).
        attribution = detect_attribution(req.claim, analysis.attributed_to, analysis.exact_words)
        check = check_attribution_evidence(verdict, stances, by_id, attribution)
        verdict, attribution_change = check.verdict, check.adjustment
        if check.passages:
            judgments, used, judged_by = await self._entailment.judge(
                req.claim, attribution.speaker, check
            )
            usage += used
            if judged_by:
                models_used["entailment"] = judged_by
            semantic_check_failed = judgments is None
            if semantic_check_failed:
                notes.append("The check of the speaker's own words could not be completed.")
            verdict, attribution_change = apply_entailment(
                check, judgments, by_id, attribution.speaker
            )
            mark = lap("entailment", mark)
        if attribution_change is not None:
            adjustments.append(attribution_change)
            # There is no longer a verdict for "rests on a single source" to qualify.
            guard_notes = [n for n in guard_notes if not n.startswith("The verdict rests on")]
            guard_notes.append(
                f"Verdict changed from '{attribution_change.from_verdict}' to "
                f"'{attribution_change.to_verdict}': {attribution_change.reason}"
            )
        notes += guard_notes

        findings: list[KeyFinding] = []
        quote_notes: list[str] = []
        misaligned = 0
        for f in evaluation.key_findings[:8]:
            known = [sid for sid in f.source_ids if sid in by_id]
            # A finding may only cite sources the evaluator itself assessed as backing it.
            ids = [sid for sid in known if stances.get(sid) in _COMPATIBLE[f.stance]]
            misaligned += len(known) - len(ids)
            text = clip(strip_unknown_citations(f.text, set(by_id)), 500)
            if ids and text:  # an uncited finding is not evidence
                findings.append(KeyFinding(text=text, stance=f.stance, source_ids=ids))
                quote_notes += check_quotations(
                    text, [by_id[sid] for sid in ids], (req.claim, normalized)
                )
        # The model's own evidence-backed findings can disagree with its verdict: "supported"
        # while one finding, backed by a source assessed as contradicting, says part of the claim
        # is false (a true fact with an unsupported cause, a real quote in misleading context).
        # The claim is then partly supported and partly contradicted: mixed, not confident.
        opposite = {"supported": "contradicts", "contradicted": "supports"}.get(verdict)
        if opposite and any(f.stance == opposite for f in findings):
            adjustments.append(
                VerdictAdjustment(
                    rule="verdict_contradicts_own_findings",
                    from_verdict=verdict,
                    to_verdict="mixed",
                    reason=(
                        "The evaluation's own findings, backed by the cited sources, "
                        f"{'contradict' if opposite == 'contradicts' else 'support'} part of the "
                        "claim, so it is neither fully supported nor fully contradicted."
                    ),
                )
            )
            notes = [n for n in notes if not n.startswith("The verdict rests on")]
            notes.append(
                f"Verdict changed from '{verdict}' to 'mixed': the findings disagree with it."
            )
            verdict = "mixed"
        if misaligned:
            notes.append(
                f"Removed {misaligned} citation(s) from key findings: the cited source was not "
                "assessed as backing that finding."
            )
        basis_ids = [
            sid
            for sid, st in stances.items()
            if (verdict, st) in {("supported", "supports"), ("contradicted", "contradicts")}
        ]
        if basis_ids and all(by_id[sid].origin == "search_snippet" for sid in basis_ids):
            notes.append(
                "The verdict rests only on search-engine snippets; the pages themselves could "
                "not be read. Open the sources to confirm."
            )

        explanation = clip(strip_unknown_citations(evaluation.explanation, set(by_id)), 1200) or ""
        cited_in_explanation = [by_id[c] for c in dict.fromkeys(_CITATION_RE.findall(explanation))]
        quote_notes += check_quotations(
            explanation, cited_in_explanation or sources, (req.claim, normalized)
        )
        notes += list(dict.fromkeys(quote_notes))
        if verdict != evaluation.verdict:
            lead = (
                "The evidence supports part of this claim and contradicts another part. "
                if verdict == "mixed"
                else "The retrieved evidence does not clearly establish a verdict. "
            )
            explanation = (lead + explanation).strip()

        # Show sources that were assessed as relevant first; irrelevant last.
        order = {
            "supports": 0,
            "contradicts": 0,
            "partially_supports": 1,
            "unclear": 2,
            None: 2,
            "irrelevant": 3,
        }
        refs = [
            SourceRef(
                id=src.id,
                title=clip(src.title, 300),
                url=src.url,
                domain=src.domain,
                published_date=src.published_date,
                retrieved_at=retrieved_at,
                excerpts=src.excerpts,
                excerpt_origin=src.origin,
                stance=stances.get(src.id),
                assessment_note=assessment_notes.get(src.id),
            )
            for src in sources
        ]
        refs.sort(key=lambda r: order[r.stance])

        limitations = [x for x in (clip(item, 400) for item in evaluation.limitations[:6]) if x]
        return finish(
            verdict=verdict,
            explanation=explanation,
            key_findings=findings,
            sources=refs,
            limitations=limitations,
            search_queries=executed_queries,
            independent_source_count=independent_domains(stances, by_id),
            guardrail_notes=notes,
            verdict_adjustments=adjustments,
        )
