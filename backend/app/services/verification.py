"""Steps C-F: analyse claim -> grounded retrieval -> evidence evaluation -> guardrails."""

from __future__ import annotations

import dataclasses
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
from app.models.llm_outputs import ClaimAnalysisOutput, EvaluationOutput
from app.models.schemas import (
    ClaimType,
    KeyFinding,
    SourceRef,
    Stance,
    Verdict,
    VerifyRequest,
    VerifyResponse,
)
from app.services import prompts
from app.services.citations import EvidenceSource, format_sources_for_prompt
from app.services.evidence import origin_groups
from app.services.llm import LLMClient, Usage
from app.services.retrieval import GeminiGroundingRetriever, RetrievalResult, Retriever
from app.services.structured import clip, parse_model_json, to_token_usage

log = logging.getLogger(__name__)

Stage = Literal["analyzing", "searching", "evaluating"]
ProgressCallback = Callable[[Stage], Awaitable[None]]

NON_CHECKABLE: set[ClaimType] = {"opinion", "prediction", "non_verifiable"}
_CITATION_RE = re.compile(r"\[(S\d{1,2})\]")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([.,;:!?])")


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


# --------------------------------------------------------------------- service


class VerificationService:
    def __init__(
        self,
        llm: LLMClient,
        settings: Settings,
        http_client: httpx.AsyncClient | None = None,
        cache: TTLCache[VerifyResponse] | None = None,
        retriever: Retriever | None = None,
    ) -> None:
        self._llm = llm
        self._settings = settings
        self._cache = cache
        self._retriever = retriever or GeminiGroundingRetriever(
            llm, settings.verification_model, http_client, settings.resolve_source_redirects
        )

    async def verify(
        self, req: VerifyRequest, progress: ProgressCallback = _noop
    ) -> VerifyResponse:
        s = self._settings
        model = s.verification_model
        cache_key = (
            f"{' '.join(req.claim.lower().split())}|{req.language}|{model}|{self._retriever.name}"
        )
        if self._cache is not None and (hit := self._cache.get(cache_key)) is not None:
            return hit.model_copy(update={"cached": True, "latency_ms": 0})

        started = time.perf_counter()
        usage = Usage()

        # --- Step A': classify + plan queries (also covers manual claims) ----
        await progress("analyzing")
        res = await self._llm.generate_json(
            model=model,
            system=prompts.ANALYSIS_SYSTEM,
            prompt=prompts.analysis_prompt(
                claim=req.claim, context=req.context, language=req.language
            ),
            schema=ClaimAnalysisOutput,
        )
        usage += res.usage
        models_used = {"analysis": res.model}
        analysis = parse_model_json(res.text, ClaimAnalysisOutput)
        normalized = clip(analysis.normalized_claim, 1000) or req.claim
        queries = [q for q in (clip(x, 200) for x in analysis.search_queries) if q][:4] or [
            normalized
        ]

        retrieval: RetrievalResult | None = None

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
                    "total_tokens": usage.total_tokens,
                },
            )
            if self._cache is not None:
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
                guardrail_notes=["No verdict was generated without retrieved evidence."],
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
                    language=req.language,
                ),
                schema=EvaluationOutput,
            )
            usage += res.usage
            models_used["evaluation"] = res.model
            evaluation = parse_model_json(res.text, EvaluationOutput)
        except AppError as exc:
            raise keep_progress(exc) from None

        # --- Step F: citation integrity + guardrails ---------------------------
        by_id = {src.id: src for src in sources}
        notes: list[str] = []
        stances: dict[str, Stance] = {}
        assessment_notes: dict[str, str | None] = {}
        dropped = 0
        for a in evaluation.source_assessments:
            if a.source_id in by_id:
                stances[a.source_id] = a.stance
                assessment_notes[a.source_id] = clip(a.note, 400)
            else:
                dropped += 1
        if dropped:
            notes.append(f"Ignored {dropped} assessment(s) that referenced unknown sources.")

        verdict, guard_notes = apply_verdict_guardrails(evaluation.verdict, stances, by_id)
        notes += guard_notes

        findings: list[KeyFinding] = []
        misaligned = 0
        for f in evaluation.key_findings[:8]:
            known = [sid for sid in f.source_ids if sid in by_id]
            # A finding may only cite sources the evaluator itself assessed as backing it.
            ids = [sid for sid in known if stances.get(sid) in _COMPATIBLE[f.stance]]
            misaligned += len(known) - len(ids)
            text = clip(strip_unknown_citations(f.text, set(by_id)), 500)
            if ids and text:  # an uncited finding is not evidence
                findings.append(KeyFinding(text=text, stance=f.stance, source_ids=ids))
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
        if verdict != evaluation.verdict:
            explanation = (
                "The retrieved evidence does not clearly establish a verdict. " + explanation
            ).strip()

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
        )
