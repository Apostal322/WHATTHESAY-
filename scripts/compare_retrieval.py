#!/usr/bin/env python3
"""Compare evidence retrieval backends on the same claims and queries.

Each backend runs on its own (no failover between them), with the fixed
queries from the case file, so results are comparable and SearXNG/Brave can be
measured without spending any Gemini quota:

    python scripts/compare_retrieval.py eval/retrieval_cases.jsonl \\
        --backends searxng,brave,gemini_grounding \\
        --searxng-url http://localhost:8888 --price-brave-1k 5 \\
        --price-in 0.75 --price-out 3.75 --price-search-1k 14

Configuration (keys, URLs) comes from .env like the backend: BRAVE_SEARCH_API_KEY,
YOUVERIFY_SEARXNG_URL, GEMINI_API_KEY. Writes eval/runs/retrieval-<ts>/results.json
and report.md (summary, per-case table, manual review sheet). Nothing is
judged automatically except cheap proxies; relevance, reliability and support
are for a person to fill in on the review sheet.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

import httpx

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.evaluation.retrieval import RetrievalCase, classify_source, summarize
from app.services.citations import EvidenceSource, is_grounding_redirect
from app.services.evidence import ClaimTerms, lexical_relevance, site_of
from app.services.llm import GeminiClient
from app.services.retrieval import GeminiGroundingRetriever, Retriever, SearchRetriever
from app.services.search import BraveProvider, FailoverSearch, SearchProvider, SearxngProvider
from app.services.structured import to_token_usage

BACKENDS = ("searxng", "brave", "gemini_grounding")


def publisher_url(src: EvidenceSource) -> str:
    """Grounding may keep Google's redirect URL; classify by the publisher domain then."""
    if is_grounding_redirect(src.url):
        return f"https://{src.domain or 'unknown.invalid'}/"
    return src.url


def build_retriever(name: str, settings: Settings, http: httpx.AsyncClient) -> Retriever:
    if name == "gemini_grounding":
        key = settings.gemini_api_key
        if not key:
            raise SystemExit("gemini_grounding needs GEMINI_API_KEY")
        llm = GeminiClient(
            key.get_secret_value(),
            timeout_sec=settings.request_timeout_sec,
            max_retries=settings.max_retries,
        )
        return GeminiGroundingRetriever(
            llm, settings.verification_model, http, settings.resolve_source_redirects
        )
    provider: SearchProvider
    if name == "searxng":
        if not settings.searxng_url:
            raise SystemExit("searxng needs --searxng-url or YOUVERIFY_SEARXNG_URL")
        provider = SearxngProvider(settings.searxng_url, http, settings.search_timeout_sec)
    elif name == "brave":
        if not settings.brave_api_key:
            raise SystemExit("brave needs BRAVE_SEARCH_API_KEY")
        provider = BraveProvider(
            settings.brave_api_key.get_secret_value(),
            http,
            settings.search_timeout_sec,
            settings.price_per_1k_brave_requests,
        )
    else:
        raise SystemExit(f"unknown backend {name!r}; choose from {BACKENDS}")
    # No cache: every case must hit the provider for a fair latency/cost measurement.
    return SearchRetriever(
        FailoverSearch([provider], failure_threshold=10_000),
        http,
        pages_to_fetch=settings.pages_to_fetch,
    )


async def run_one(
    backend: str, retriever: Retriever, case: RetrievalCase, settings: Settings
) -> dict[str, Any]:
    row: dict[str, Any] = {"backend": backend, "case": case.id, "category": case.category}
    started = time.perf_counter()
    try:
        res = await retriever.retrieve(
            claim=case.claim, queries=case.queries, scope_notes=None, language=case.language
        )
    except AppError as exc:
        row.update(
            error=exc.code,
            diagnostics=exc.diagnostics,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return row
    row["latency_ms"] = int((time.perf_counter() - started) * 1000)
    searches = res.diagnostics.get("searches", [])
    if backend != "gemini_grounding" and res.backend == "none":
        reasons = sorted({a["error"] for q in searches for a in q["attempts"] if a["error"]})
        if reasons:  # every search failed: a provider failure, not "no evidence"
            row.update(error="all_searches_failed", diagnostics={"reasons": reasons})
            return row
    terms = ClaimTerms.build(case.claim, case.queries)
    usage = to_token_usage(res.usage, settings, res.billed_searches, res.search_cost_usd)
    row.update(
        billed_searches=res.billed_searches,
        tokens={"prompt": res.usage.prompt_tokens, "output": res.usage.output_tokens},
        cost_usd=usage.estimated_cost_usd if backend == "gemini_grounding" else res.search_cost_usd,
        empty_reason=None if res.sources else res.empty_reason,
        retrieval_diagnostics=res.diagnostics,
        sources=[
            {
                "id": s.id,
                "url": s.url,
                "domain": s.domain,
                "site": site_of(publisher_url(s)),
                "source_type": classify_source(publisher_url(s)),
                "origin": s.origin,
                "relevance": round(
                    max((lexical_relevance(x, terms) for x in s.excerpts), default=0.0), 3
                ),
                "excerpts": s.excerpts,
            }
            for s in res.sources
        ],
    )
    return row


def _md(text: Any, n: int = 90) -> str:
    s = "" if text is None else str(text).replace("|", "\\|").replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def render_report(
    meta: dict[str, Any], cases: list[RetrievalCase], rows: list[dict[str, Any]]
) -> str:
    summary = summarize(rows)
    lines = [
        "# Retrieval comparison",
        "",
        f"- Run: {meta['run_at']} · backends: {', '.join(meta['backends'])} · cases: {len(cases)}",
        f"- Prices (USD): {meta['prices']}",
        "- Automatic columns are proxies: *source type* is a domain heuristic, *relevance* is lexical",
        "  overlap with the claim. Relevance, reliability and support are judged on the review sheet.",
        "- Draft verdicts are the author's expectation, **not ground truth**, unless `verified_by` is set.",
        "",
        "## Summary (successful runs only)",
        "",
        "| backend | cases | failed | with sources | median latency ms | sources/claim | independent sites/claim | page-text share | mean excerpt relevance | source types | cost $/claim (known for) |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for b, st in summary.items():
        lines.append(
            f"| {b} | {st['cases']} | {st['failed']} | {st['with_sources']} | {st['median_latency_ms']} | "
            f"{st['mean_sources']} | {st['mean_independent_sites']} | {st['page_text_share']} | "
            f"{st['mean_excerpt_relevance']} | {_md(st['source_types'], 120)} | "
            f"{st['cost_usd_per_claim']} ({st['cost_known_for']}) |"
        )
    lines += [
        "",
        "## Per case",
        "",
        "| case | backend | latency ms | sources | sites | types | cost $ | error / note |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        srcs = r.get("sources") or []
        types = ", ".join(sorted({s["source_type"] for s in srcs}))
        note = r.get("error") or r.get("empty_reason") or ""
        if r.get("error") and r.get("diagnostics"):
            d = r["diagnostics"]
            note += f" ({_md(d.get('reasons') or d.get('provider_status') or d, 80)})"
        lines.append(
            f"| {r['case']} | {r['backend']} | {r.get('latency_ms')} | {len(srcs)} | "
            f"{len({s['site'] for s in srcs})} | {types} | {r.get('cost_usd')} | {_md(note, 100)} |"
        )
    lines += ["", "## Review sheet (fill in by hand)", ""]
    by_case = {c.id: c for c in cases}
    for cid in dict.fromkeys(r["case"] for r in rows):
        c = by_case[cid]
        verified = f"verified by {c.verified_by}" if c.verified_by else "draft, unverified"
        lines += [f"### {cid}: {c.claim}", "", f"Draft verdict: {c.draft_verdict} ({verified})", ""]
        lines += [
            "| backend | id | type | origin | url | excerpt | relevant? | reliable? | supports / contradicts / neither |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for r in rows:
            if r["case"] != cid:
                continue
            for s in r.get("sources") or []:
                lines.append(
                    f"| {r['backend']} | {s['id']} | {s['source_type']} | {s['origin']} | {_md(s['url'], 60)} | "
                    f"{_md(' / '.join(s['excerpts']), 140)} | | | |"
                )
        lines.append("")
    return "\n".join(lines) + "\n"


async def run_all(
    args: argparse.Namespace, cases: list[RetrievalCase], settings: Settings
) -> list[dict[str, Any]]:
    rows = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as http:
        retrievers = {b: build_retriever(b, settings, http) for b in args.backends}
        for case in cases:
            for backend, retriever in retrievers.items():
                print(f"[{case.id}] {backend}…", file=sys.stderr)
                rows.append(await run_one(backend, retriever, case, settings))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("cases")
    ap.add_argument("--backends", default="searxng,brave,gemini_grounding")
    ap.add_argument("--only", help="Comma-separated case ids")
    ap.add_argument("--searxng-url")
    ap.add_argument("--price-brave-1k", type=float, help="USD per 1000 Brave requests")
    ap.add_argument("--price-in", type=float, help="USD per 1M Gemini input tokens")
    ap.add_argument("--price-out", type=float, help="USD per 1M Gemini output tokens")
    ap.add_argument("--price-search-1k", type=float, help="USD per 1000 grounding searches")
    args = ap.parse_args()
    args.backends = [b.strip() for b in args.backends.split(",") if b.strip()]

    cases = [
        RetrievalCase.model_validate(json.loads(line))
        for line in Path(args.cases).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.only:
        wanted = {i.strip() for i in args.only.split(",")}
        cases = [c for c in cases if c.id in wanted]

    updates: dict[str, Any] = {}
    for flag, field in (
        ("searxng_url", "searxng_url"),
        ("price_brave_1k", "price_per_1k_brave_requests"),
        ("price_in", "price_input_per_million"),
        ("price_out", "price_output_per_million"),
        ("price_search_1k", "price_per_1k_search_queries"),
    ):
        if getattr(args, flag) is not None:
            updates[field] = getattr(args, flag)
    settings = get_settings().model_copy(update=updates)
    configure_logging("WARNING")

    rows = asyncio.run(run_all(args, cases, settings))
    meta = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "backends": args.backends,
        "prices": {
            "brave_per_1k": settings.price_per_1k_brave_requests,
            "gemini_in_per_M": settings.price_input_per_million,
            "gemini_out_per_M": settings.price_output_per_million,
            "grounding_search_per_1k": settings.price_per_1k_search_queries,
        },
    }
    out = REPO / "eval" / "runs" / f"retrieval-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(
        json.dumps(
            {"meta": meta, "summary": summarize(rows), "rows": rows}, indent=2, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    (out / "report.md").write_text(render_report(meta, cases, rows), encoding="utf-8")
    print(f"wrote {out.relative_to(REPO)}/report.md", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
