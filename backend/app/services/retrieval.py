"""Step C: evidence retrieval behind one interface.

`Retriever` returns numbered, citable `EvidenceSource`s for a claim. The
verification pipeline (analysis -> retrieval -> evaluation -> guardrails) does
not know which implementation produced them:

- `GeminiGroundingRetriever`: Gemini + Google Search grounding (the original
  behaviour; excerpts are grounding-attributed model summaries).
- `SearchRetriever`: any `SearchProvider` chain (SearXNG, Brave, ...), then
  deterministic ranking, page fetching and verbatim passage extraction
  (`evidence.py`). No model call, so retrieval cost is the search fee only.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.core.cache import TTLCache
from app.core.errors import RetrievalUnavailableError
from app.services import prompts
from app.services.citations import (
    EvidenceSource,
    build_sources,
    dedupe_and_number,
    normalize_domain,
    resolve_redirects,
)
from app.services.evidence import (
    ClaimTerms,
    RankedHit,
    excerpt_is_verbatim,
    extract_passages,
    rank_hits,
)
from app.services.fetch import FetchError, fetch_page
from app.services.llm import LLMClient, Usage
from app.services.search import FailoverOutcome, FailoverSearch, SearchHit

log = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    sources: list[EvidenceSource]  # numbered S1..Sn, ready for the evaluator
    executed_queries: list[str]
    backend: str  # which retrieval actually served the request, e.g. "searxng"
    usage: Usage = field(default_factory=Usage)  # model tokens spent on retrieval
    billed_searches: int = 0
    # Search fees in USD when known. None for grounding: priced from settings downstream.
    search_cost_usd: float | None = None
    empty_reason: str = "No citable sources were retrieved."
    model: str | None = None  # model that served retrieval, when a model was involved
    diagnostics: dict[str, Any] = field(default_factory=dict)


class Retriever(Protocol):
    name: str

    async def retrieve(
        self, *, claim: str, queries: list[str], scope_notes: str | None, language: str
    ) -> RetrievalResult: ...


class GeminiGroundingRetriever:
    name = "gemini_grounding"

    def __init__(
        self,
        llm: LLMClient,
        model: str,
        http_client: httpx.AsyncClient | None = None,
        resolve_source_redirects: bool = True,
    ) -> None:
        self._llm = llm
        self._model = model
        self._http = http_client
        self._resolve = resolve_source_redirects

    async def retrieve(
        self, *, claim: str, queries: list[str], scope_notes: str | None, language: str
    ) -> RetrievalResult:
        grounded = await self._llm.grounded_search(
            model=self._model,
            system=prompts.RETRIEVAL_SYSTEM,
            prompt=prompts.retrieval_prompt(claim=claim, queries=queries, scope_notes=scope_notes),
        )
        if not grounded.raw_metadata_present:
            # The model answered without running a search: nothing was retrieved.
            raise RetrievalUnavailableError(
                "The search step did not run, so no evidence was retrieved. Try again.",
                diagnostics={"backend": self.name, "reason": "no_grounding_metadata"},
            )
        candidates = build_sources(grounded)
        if candidates and self._http is not None and self._resolve:
            await resolve_redirects(candidates, self._http)
        # Only queries Google reports as executed: these are what grounding bills.
        executed = list(grounded.queries)
        return RetrievalResult(
            sources=dedupe_and_number(candidates),
            executed_queries=executed,
            backend=self.name,
            usage=grounded.usage,
            model=grounded.model,
            billed_searches=len(executed),
            empty_reason="No grounded search results with attributable content were retrieved.",
        )


class SearchRetriever:
    name = "search"

    def __init__(
        self,
        search: FailoverSearch,
        http_client: httpx.AsyncClient,
        *,
        results_per_query: int = 10,
        pages_to_fetch: int = 6,
        max_sources: int = 8,
        fetch_timeout_sec: float = 8.0,
        cache: TTLCache[FailoverOutcome] | None = None,
        check_dns: bool = True,
    ) -> None:
        self._search = search
        self._http = http_client
        self._per_query = results_per_query
        self._pages = pages_to_fetch
        self._max_sources = max_sources
        self._fetch_timeout = fetch_timeout_sec
        self._cache = cache
        self._check_dns = check_dns
        self._prices = {p.name: p.price_per_1k_requests for p in search.providers}

    async def _search_one(self, query: str, language: str) -> tuple[FailoverOutcome, bool]:
        key = f"{query.strip().lower()}|{language}"
        if self._cache is not None and (hit := self._cache.get(key)) is not None:
            return hit, True
        outcome = await self._search.search(query, language=language, max_results=self._per_query)
        if self._cache is not None and outcome.response is not None:
            self._cache.set(key, outcome)
        return outcome, False

    async def _to_source(
        self, item: RankedHit, terms: ClaimTerms
    ) -> tuple[EvidenceSource | None, str]:
        try:
            page = await asyncio.wait_for(
                fetch_page(item.url, self._http, check_dns=self._check_dns),
                timeout=self._fetch_timeout,
            )
        except (FetchError, TimeoutError) as exc:
            reason = exc.reason if isinstance(exc, FetchError) else "timeout"
            if item.snippet:  # fall back to the snippet, labelled as such
                return (
                    EvidenceSource(
                        id="",
                        url=item.url,
                        title=item.title,
                        domain=normalize_domain(item.url),
                        excerpts=[item.snippet[:400]],
                        origin="search_snippet",
                    ),
                    f"fetch_failed:{reason}",
                )
            return None, f"fetch_failed:{reason}"
        passages = [
            p
            for p in extract_passages(page.paragraphs, terms)
            if excerpt_is_verbatim(p, page.paragraphs)
        ]
        if not passages:
            return None, "no_relevant_passage"
        return (
            EvidenceSource(
                id="",
                url=page.url,
                title=item.title,
                domain=normalize_domain(page.url),
                excerpts=passages,
                origin="page_text",
            ),
            "page_text",
        )

    async def retrieve(
        self, *, claim: str, queries: list[str], scope_notes: str | None, language: str
    ) -> RetrievalResult:
        queries = queries or [claim]
        outcomes = await asyncio.gather(*(self._search_one(q, language) for q in queries))

        hits: dict[str, list[SearchHit]] = {}
        served_by: list[str] = []
        billed = 0
        cost: float | None = 0.0
        attempts: list[dict[str, Any]] = []
        degraded: set[str] = set()
        for query, (outcome, cached) in zip(queries, outcomes, strict=True):
            attempts.append(
                {
                    "query": query,
                    "cached": cached,
                    "attempts": [a.__dict__ for a in outcome.attempts],
                }
            )
            if not cached:  # a cache hit costs nothing
                for att in outcome.attempts:
                    if not att.ok:
                        continue  # failed requests (timeouts, 429s) are not billed
                    billed += 1
                    price = self._prices.get(att.provider)
                    # One request with an unknown price makes the total unknown, not lower.
                    cost = None if price is None or cost is None else cost + price / 1000
            if outcome.response is not None:
                hits[query] = outcome.response.hits
                served_by.append(outcome.response.provider)
                degraded.update(outcome.response.degraded_engines)

        errors = sorted({a.error for o, _ in outcomes for a in o.attempts if a.error})
        if not hits and errors:
            # Every query failed on every provider: no search happened, so this is an
            # outage, not a lack of evidence.
            budget_only = set(errors) == {"budget_exhausted"}
            err = RetrievalUnavailableError(
                "The daily evidence-search capacity has been reached. Try again tomorrow."
                if budget_only
                else "Evidence search is temporarily unavailable. Try again shortly.",
                diagnostics={"stage": "search", "provider_errors": errors, "searches": attempts},
            )
            err.retryable = not budget_only
            raise err

        terms = ClaimTerms.build(claim, queries)
        ranked = rank_hits(hits, terms, limit=self._pages)
        converted = await asyncio.gather(*(self._to_source(item, terms) for item in ranked))
        sources = [src for src, _ in converted if src is not None][: self._max_sources]
        statuses = [status for _, status in converted]
        if ranked and not sources and all(st.startswith("fetch_failed") for st in statuses):
            # Results existed but no page could be read (and none had a snippet).
            raise RetrievalUnavailableError(
                "The evidence pages could not be retrieved. Try again shortly.",
                diagnostics={"stage": "fetch", "fetch": statuses, "searches": attempts},
            )
        backend = "+".join(dict.fromkeys(served_by)) or "none"
        if not hits:
            empty = "The search returned no results for this claim."
        elif not ranked:
            empty = "Search results were found, but none were relevant to the claim."
        else:
            empty = "Relevant pages were read, but none contained a passage about the claim."
        return RetrievalResult(
            sources=dedupe_and_number(sources),
            executed_queries=list(hits),
            backend=backend,
            billed_searches=billed,
            search_cost_usd=round(cost, 6) if cost is not None else None,
            empty_reason=empty,
            diagnostics={
                "searches": attempts,
                "ranked": [
                    {
                        "url": r.url,
                        "site": r.site,
                        "relevance": round(r.relevance, 3),
                        "score": round(r.score, 5),
                    }
                    for r in ranked
                ],
                "fetch": statuses,
                "degraded_engines": sorted(degraded),
            },
        )
