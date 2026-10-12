"""Steps C-F: analyse claim -> grounded retrieval -> evidence evaluation -> guardrails."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Literal

import httpx

from app.core.cache import TTLCache
from app.core.config import Settings
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
from app.services.citations import (
    EvidenceSource,
    build_sources,
    dedupe_and_number,
    format_sources_for_prompt,
    resolve_redirects,
)
from app.services.llm import LLMClient, Usage
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
        domains = {sources[sid].domain or sources[sid].url for sid in basis}
        if len(domains) == 1:
            notes.append("The verdict rests on a single source domain; treat it with caution.")
    if final == "supported" and contradicts:
        notes.append("Some retrieved sources contradict the claim; see the evidence list.")
    if final == "contradicted" and supports:
        notes.append("Some retrieved sources support the claim; see the evidence list.")
    return final, notes


def strip_unknown_citations(text: str, known: set[str]) -> str:
    stripped = _CITATION_RE.sub(lambda m: m.group(0) if m.group(1) in known else "", text)
    return _SPACE_BEFORE_PUNCT_RE.sub(r"\1", re.sub(r" {2,}", " ", stripped)).strip()


def independent_domains(stances: dict[str, Stance], sources: dict[str, EvidenceSource]) -> int:
    used = {sid for sid, st in stances.items() if st != "irrelevant"}
    return len({sources[sid].domain or sources[sid].url for sid in used})


# --------------------------------------------------------------------- service


class VerificationService:
    def __init__(
        self,
        llm: LLMClient,
        settings: Settings,
        http_client: httpx.AsyncClient | None = None,
        cache: TTLCache[VerifyResponse] | None = None,
    ) -> None:
        self._llm = llm
        self._settings = settings
        self._http = http_client
        self._cache = cache

    async def verify(
        self, req: VerifyRequest, progress: ProgressCallback = _noop
    ) -> VerifyResponse:
        s = self._settings
        model = s.verification_model
        cache_key = f"{' '.join(req.claim.lower().split())}|{req.language}|{model}"
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
        analysis = parse_model_json(res.text, ClaimAnalysisOutput)
        normalized = clip(analysis.normalized_claim, 1000) or req.claim
        queries = [q for q in (clip(x, 200) for x in analysis.search_queries) if q][:4] or [
            normalized
        ]

        def finish(**kw: object) -> VerifyResponse:
            resp = VerifyResponse(
                claim=req.claim,
                normalized_claim=normalized,
                claim_type=analysis.claim_type,
                model=model,
                latency_ms=int((time.perf_counter() - started) * 1000),
                usage=to_token_usage(usage, s),
                **kw,
            )
            log.info(
                "claim_verified",
                extra={
                    "verdict": resp.verdict,
                    "claim_type": resp.claim_type,
                    "sources": len(resp.sources),
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

        # --- Step C: grounded retrieval --------------------------------------
        await progress("searching")
        grounded = await self._llm.grounded_search(
            model=model,
            system=prompts.RETRIEVAL_SYSTEM,
            prompt=prompts.retrieval_prompt(
                claim=normalized, queries=queries, scope_notes=analysis.scope_notes
            ),
        )
        usage += grounded.usage
        retrieved_at = datetime.now(UTC).isoformat(timespec="seconds")
        candidates = build_sources(grounded)
        if candidates and self._http is not None and s.resolve_source_redirects:
            await resolve_redirects(candidates, self._http)
        sources = dedupe_and_number(candidates)
        search_queries = grounded.queries or queries

        if not sources:
            return finish(
                verdict="insufficient_evidence",
                explanation=(
                    "The search did not return any citable sources for this claim, so no "
                    "evidence-based verdict can be given."
                ),
                key_findings=[],
                sources=[],
                limitations=[
                    "No grounded search results with attributable content were retrieved."
                    if grounded.raw_metadata_present
                    else "The search step returned no grounding metadata."
                ],
                search_queries=search_queries,
                independent_source_count=0,
                guardrail_notes=["No verdict was generated without retrieved evidence."],
            )

        # --- Step D/E: evaluation ---------------------------------------------
        await progress("evaluating")
        res = await self._llm.generate_json(
            model=model,
            system=prompts.EVALUATION_SYSTEM,
            prompt=prompts.evaluation_prompt(
                claim=normalized,
                scope_notes=analysis.scope_notes,
                sources_block=format_sources_for_prompt(sources),
                language=req.language,
            ),
            schema=EvaluationOutput,
        )
        usage += res.usage
        evaluation = parse_model_json(res.text, EvaluationOutput)

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
        for f in evaluation.key_findings[:8]:
            ids = [sid for sid in f.source_ids if sid in by_id]
            text = clip(strip_unknown_citations(f.text, set(by_id)), 500)
            if ids and text:  # an uncited finding is not evidence
                findings.append(KeyFinding(text=text, stance=f.stance, source_ids=ids))

        explanation = clip(strip_unknown_citations(evaluation.explanation, set(by_id)), 1200) or ""
        if verdict != evaluation.verdict:
            explanation = (
                "The retrieved evidence does not clearly establish a verdict. " + explanation
            ).strip()

        # Show sources that were assessed as relevant first; irrelevant last.
        order = {"supports": 0, "contradicts": 0, "partially_supports": 1, None: 2, "irrelevant": 3}
        refs = [
            SourceRef(
                id=src.id,
                title=clip(src.title, 300),
                url=src.url,
                domain=src.domain,
                retrieved_at=retrieved_at,
                excerpts=src.excerpts,
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
            search_queries=search_queries,
            independent_source_count=independent_domains(stances, by_id),
            guardrail_notes=notes,
        )
