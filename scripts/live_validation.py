#!/usr/bin/env python3
"""Live integration validation against the real Gemini API (no mocks).

One command runs every check needed to confirm the core workflow:
  1. preflight: the configured models are available to your key
  2. extraction at each human-verified timestamp (product path, default window)
  3. clipping experiment: 20 s vs 50 s windows on the same moment, comparing
     media tokens, latency, accuracy and cost
  4. verification of each claim, with an optional fetch of every cited page to
     screen whether the excerpts are actually reflected there

    cp eval/live_cases.template.jsonl eval/live_cases.jsonl   # fill in real, verified moments
    python scripts/live_validation.py eval/live_cases.jsonl --fetch-sources \\
        --price-in 0.75 --price-out 3.75 --price-search-1k 14

Writes eval/runs/live-<timestamp>/results.json and report.md. Spends API credits.
Prices are taken from the flags or from YOUVERIFY_PRICE_* settings. Check them
against https://ai.google.dev/gemini-api/docs/pricing; nothing is assumed.
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
from app.evaluation.live import (
    ClipRun,
    LiveCase,
    clipping_verdict,
    expected_clip_tokens,
    html_to_text,
    lexical_support,
    needs_review,
)
from app.evaluation.metrics import token_f1
from app.models.llm_outputs import ExtractionOutput
from app.models.schemas import ExtractRequest, VerifyRequest
from app.services.citations import is_safe_display_url
from app.services.extraction import ExtractionService
from app.services.llm import GeminiClient, LLMClient, Usage, VideoClip
from app.services.prompts import EXTRACTION_SYSTEM, extraction_prompt
from app.services.structured import parse_model_json, to_token_usage
from app.services.verification import VerificationService
from app.services.youtube import canonical_watch_url, parse_video_id

CLIP_WINDOWS = ((15, 5), (45, 5))  # (before, after) -> 20 s and 50 s
MAX_PAGE_BYTES = 2_000_000


def _cost(usage: Usage, settings: Settings, searches: int = 0) -> float | None:
    return to_token_usage(usage, settings, searches).estimated_cost_usd


async def run_extraction(case: LiveCase, svc: ExtractionService) -> dict[str, Any]:
    started = time.perf_counter()
    resp = await svc.extract(
        ExtractRequest(
            youtube_url=case.youtube_url,
            timestamp_sec=case.timestamp_sec,
            duration_sec=case.duration_sec,
            language=case.language,
        )
    )
    return {
        "status": resp.status,
        "claim": resp.claim,
        "claim_type": resp.claim_type,
        "evidence_from_video": resp.evidence_from_video,
        "is_exact_quote": resp.is_exact_quote,
        "reason": resp.reason,
        "claim_f1": round(token_f1(resp.claim or "", case.ground_truth_claim), 3),
        "quote_f1": round(token_f1(resp.evidence_from_video or "", case.ground_truth_claim), 3),
        "window": resp.window.model_dump(),
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "usage": resp.usage.model_dump(),
    }


async def run_clipping(case: LiveCase, llm: LLMClient, settings: Settings) -> dict[str, Any]:
    video_id = parse_video_id(case.youtube_url)
    t = int(case.timestamp_sec)
    runs: list[dict[str, Any]] = []
    for before, after in CLIP_WINDOWS:
        start, end = max(0, t - before), t + after
        began = time.perf_counter()
        res = await llm.generate_json(
            model=settings.extraction_model,
            system=EXTRACTION_SYSTEM,
            prompt=extraction_prompt(
                timestamp_sec=t, start_sec=start, end_sec=end, language=case.language
            ),
            schema=ExtractionOutput,
            video=VideoClip(
                url=canonical_watch_url(video_id),
                start_sec=start,
                end_sec=end,
                fps=settings.video_fps,
                media_resolution=settings.video_media_resolution,
            ),
        )
        latency = int((time.perf_counter() - began) * 1000)
        try:
            out = parse_model_json(res.text, ExtractionOutput)
            claim, f1 = out.claim, round(token_f1(out.claim or "", case.ground_truth_claim), 3)
        except AppError as exc:
            claim, f1 = f"<{exc.code}>", 0.0
        runs.append(
            {
                "window_sec": end - start,
                "prompt_tokens": res.usage.prompt_tokens,
                "video_tokens": res.usage.video_tokens,
                "audio_tokens": res.usage.audio_tokens,
                "output_tokens": res.usage.output_tokens,
                "expected_media_tokens_if_clipped": expected_clip_tokens(
                    end - start, settings.video_fps, settings.video_media_resolution
                ),
                "latency_ms": latency,
                "claim": claim,
                "claim_f1": f1,
                "estimated_cost_usd": _cost(res.usage, settings),
            }
        )
    a, b = (
        ClipRun(r["window_sec"], r["prompt_tokens"], r["video_tokens"], r["audio_tokens"])
        for r in runs
    )
    verdict, detail = clipping_verdict(a, b)
    full = (
        expected_clip_tokens(case.duration_sec, settings.video_fps, settings.video_media_resolution)
        if case.duration_sec
        else None
    )
    return {
        "runs": runs,
        "verdict": verdict,
        "detail": detail,
        "expected_media_tokens_full_video": full,
    }


async def check_source_page(
    url: str, excerpts: list[str], http: httpx.AsyncClient
) -> dict[str, Any]:
    """Fetch a cited page (developer-run script, public URLs only) and screen excerpts."""
    if not is_safe_display_url(url):
        return {"fetched": False, "error": "unsafe_url"}
    try:
        resp = await http.get(url, follow_redirects=True)
    except httpx.HTTPError as exc:
        return {"fetched": False, "error": type(exc).__name__}
    final = str(resp.url)
    if not is_safe_display_url(final):
        return {"fetched": False, "error": "redirected_to_unsafe_url"}
    if resp.status_code >= 400:
        return {"fetched": False, "error": f"http_{resp.status_code}", "final_url": final}
    ctype = resp.headers.get("content-type", "")
    if "html" not in ctype and "text" not in ctype:
        return {"fetched": False, "error": f"non_text:{ctype[:40]}", "final_url": final}
    text = html_to_text(resp.text[:MAX_PAGE_BYTES])
    checks = [lexical_support(ex, text) for ex in excerpts]
    return {
        "fetched": True,
        "final_url": final,
        "page_chars": len(text),
        "excerpt_checks": checks,
        "needs_manual_review": any(needs_review(c) for c in checks),
    }


async def run_verification(
    case: LiveCase,
    claim: str,
    svc: VerificationService,
    http: httpx.AsyncClient | None,
) -> dict[str, Any]:
    started = time.perf_counter()
    resp = await svc.verify(VerifyRequest(claim=claim, language=case.language))
    sources = []
    for s in resp.sources:
        entry: dict[str, Any] = s.model_dump()
        if http is not None:
            entry["page_check"] = await check_source_page(s.url, s.excerpts, http)
        sources.append(entry)
    return {
        "claim_verified": claim,
        "verdict": resp.verdict,
        "reference_verdict": case.reference_verdict,
        "explanation": resp.explanation,
        "key_findings": [f.model_dump() for f in resp.key_findings],
        "limitations": resp.limitations,
        "guardrail_notes": resp.guardrail_notes,
        "search_queries": resp.search_queries,
        "independent_source_count": resp.independent_source_count,
        "sources": sources,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "usage": resp.usage.model_dump(),
    }


async def run_case(
    case: LiveCase,
    llm: LLMClient,
    settings: Settings,
    http: httpx.AsyncClient,
    args: argparse.Namespace,
) -> dict[str, Any]:
    out: dict[str, Any] = {"case": case.model_dump()}
    stages = (
        ("extraction", lambda: run_extraction(case, ExtractionService(llm, settings))),
        ("clipping", None if args.skip_clipping else lambda: run_clipping(case, llm, settings)),
    )
    for name, fn in stages:
        if fn is None:
            continue
        try:
            out[name] = await fn()
        except AppError as exc:
            out[name] = {"error": exc.code, "message": exc.message}
    if not args.skip_verify:
        extracted = (out.get("extraction") or {}).get("claim")
        claim = (
            extracted if args.verify_from == "extracted" and extracted else case.ground_truth_claim
        )
        try:
            out["verification"] = await run_verification(
                case,
                claim,
                VerificationService(llm, settings, http),
                http if args.fetch_sources else None,
            )
        except AppError as exc:
            out["verification"] = {"error": exc.code, "message": exc.message}
    return out


# ------------------------------------------------------------------ report


def _fmt(v: Any, digits: int = 4) -> str:
    if v is None:
        return "–"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


def _short(text: Any, n: int = 70) -> str:
    s = "" if text is None else str(text).replace("|", "\\|").replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def render_report(meta: dict[str, Any], results: list[dict[str, Any]]) -> str:
    lines = [
        "# Live validation report",
        "",
        f"- Run: {meta['run_at']}",
        f"- Extraction model: `{meta['extraction_model']}` · verification model: `{meta['verification_model']}`",
        f"- Window defaults: {meta['window']} · fps {meta['fps']} · media resolution {meta['resolution']}",
        f"- Prices (USD): {meta['prices']}",
        f"- Model preflight: {meta['preflight']}",
        "",
        "## Extraction (product path)",
        "",
        "| case | lang | t | ground truth | extracted | claim F1 | exact quote? | latency ms | prompt tok | cost $ |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        c, e = r["case"], r.get("extraction", {})
        if "error" in e:
            lines.append(
                f"| {c['id']} | {c['language']} | {c['timestamp_sec']} | {_short(c['ground_truth_claim'])} | **{e['error']}** | | | | | |"
            )
            continue
        lines.append(
            f"| {c['id']} | {c['language']} | {c['timestamp_sec']} | {_short(c['ground_truth_claim'])} | "
            f"{_short(e.get('claim') or e.get('reason'))} | {_fmt(e.get('claim_f1'), 2)} | {e.get('is_exact_quote')} | "
            f"{e.get('latency_ms')} | {e.get('usage', {}).get('prompt_tokens')} | {_fmt(e.get('usage', {}).get('estimated_cost_usd'))} |"
        )

    lines += [
        "",
        "## Clipping experiment (20 s vs 50 s)",
        "",
        "| case | window s | prompt tok | video tok | audio tok | expected media tok if clipped | latency ms | claim F1 | cost $ | verdict |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        cl = r.get("clipping")
        if not cl:
            continue
        if "error" in cl:
            lines.append(f"| {r['case']['id']} | | | | | | | | | **{cl['error']}** |")
            continue
        for i, run in enumerate(cl["runs"]):
            verdict = f"**{cl['verdict']}** ({cl['detail']})" if i == len(cl["runs"]) - 1 else ""
            lines.append(
                f"| {r['case']['id']} | {run['window_sec']} | {run['prompt_tokens']} | {_fmt(run['video_tokens'])} | "
                f"{_fmt(run['audio_tokens'])} | {run['expected_media_tokens_if_clipped']} | {run['latency_ms']} | "
                f"{_fmt(run['claim_f1'], 2)} | {_fmt(run['estimated_cost_usd'])} | {verdict} |"
            )

    lines += [
        "",
        "## Verification",
        "",
        "| case | claim checked | verdict | reference | sources | indep. domains | searches | pages needing review | latency ms | cost $ |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        v = r.get("verification")
        if not v:
            continue
        if "error" in v:
            lines.append(f"| {r['case']['id']} | | **{v['error']}** | | | | | | | |")
            continue
        checked = [s.get("page_check") for s in v["sources"] if s.get("page_check")]
        review = (
            f"{sum(1 for p in checked if not p.get('fetched') or p.get('needs_manual_review'))}/{len(checked)}"
            if checked
            else "not fetched"
        )
        lines.append(
            f"| {r['case']['id']} | {_short(v['claim_verified'], 50)} | **{v['verdict']}** | {_fmt(v['reference_verdict'])} | "
            f"{len(v['sources'])} | {v['independent_source_count']} | {v['usage'].get('search_queries')} | {review} | "
            f"{v['latency_ms']} | {_fmt(v['usage'].get('estimated_cost_usd'))} |"
        )

    lines += ["", "## Source review sheet (fill in the last column by hand)", ""]
    for r in results:
        v = r.get("verification")
        if not v or "error" in v:
            continue
        lines += [
            f"### {r['case']['id']}: {v['verdict']}",
            "",
            f"> {_short(v['explanation'], 600)}",
            "",
        ]
        lines += [
            "| id | stance | url | excerpt | page screen | supports conclusion? (Y/N/Partial) |",
            "|---|---|---|---|---|---|",
        ]
        for s in v["sources"]:
            pc = s.get("page_check") or {}
            if not pc:
                screen = "not fetched"
            elif not pc.get("fetched"):
                screen = f"fetch failed: {pc.get('error')}"
            else:
                worst = (
                    min(pc["excerpt_checks"], key=lambda c: c["content_coverage"])
                    if pc["excerpt_checks"]
                    else None
                )
                screen = (
                    f"coverage {worst['content_coverage']}, numbers {worst['numbers_found']}/{worst['numbers']}"
                    + (" ⚠️ review" if pc.get("needs_manual_review") else "")
                    if worst
                    else "no excerpts"
                )
            lines.append(
                f"| {s['id']} | {s.get('stance')} | {_short(s['url'], 60)} | {_short(' / '.join(s['excerpts']), 120)} | {screen} | |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


# -------------------------------------------------------------------- main


def preflight_models(api_key: str, wanted: set[str]) -> tuple[bool, str]:
    from google import genai

    client = genai.Client(api_key=api_key)
    names = {
        (m.name or "").removeprefix("models/")
        for m in client.models.list()
        if "generateContent" in (getattr(m, "supported_actions", None) or [])
    }
    missing = sorted(wanted - names)
    if missing:
        close = sorted(n for n in names if "flash" in n)[:12]
        return False, f"missing {missing}; available flash models: {close}"
    return True, f"available: {sorted(wanted)}"


async def run_all(
    cases: list[LiveCase], llm: LLMClient, settings: Settings, args: argparse.Namespace
) -> list[dict[str, Any]]:
    results = []
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(15.0), headers={"User-Agent": "YouVerify-validation/0.1"}
    ) as http:
        for case in cases:
            print(f"[{case.id}] running…", file=sys.stderr)
            results.append(await run_case(case, llm, settings, http, args))
    return results


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "cases", help="JSONL of human-verified moments (see eval/live_cases.template.jsonl)"
    )
    ap.add_argument(
        "--fetch-sources", action="store_true", help="Fetch cited pages and screen excerpts"
    )
    ap.add_argument("--skip-clipping", action="store_true")
    ap.add_argument("--skip-verify", action="store_true")
    ap.add_argument("--verify-from", choices=["ground_truth", "extracted"], default="ground_truth")
    ap.add_argument("--skip-preflight", action="store_true")
    ap.add_argument("--price-in", type=float, help="USD per 1M input tokens")
    ap.add_argument("--price-out", type=float, help="USD per 1M output tokens")
    ap.add_argument("--price-search-1k", type=float, help="USD per 1000 grounding search queries")
    args = ap.parse_args()

    lines = Path(args.cases).read_text(encoding="utf-8").splitlines()
    all_cases = [LiveCase.model_validate(json.loads(line)) for line in lines if line.strip()]
    cases = [c for c in all_cases if not c.is_placeholder()]
    if not cases:
        print("No filled-in cases (rows containing 'REPLACE' are skipped).", file=sys.stderr)
        return 2

    base = get_settings()
    updates: dict[str, Any] = {"cache_ttl_sec": 0}
    for flag, field in (
        ("price_in", "price_input_per_million"),
        ("price_out", "price_output_per_million"),
        ("price_search_1k", "price_per_1k_search_queries"),
    ):
        if getattr(args, flag) is not None:
            updates[field] = getattr(args, flag)
    settings = base.model_copy(update=updates)
    key = settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else None
    if not key:
        print("GEMINI_API_KEY is not set (see .env.example). Nothing was run.", file=sys.stderr)
        return 1

    preflight = "skipped"
    if not args.skip_preflight:
        ok, preflight = preflight_models(
            key, {settings.extraction_model, settings.verification_model}
        )
        print(f"preflight: {preflight}", file=sys.stderr)
        if not ok:
            return 1

    llm = GeminiClient(
        key, timeout_sec=settings.request_timeout_sec, max_retries=settings.max_retries
    )
    results = asyncio.run(run_all(cases, llm, settings, args))

    meta = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "extraction_model": settings.extraction_model,
        "verification_model": settings.verification_model,
        "window": f"-{settings.window_before_sec}s/+{settings.window_after_sec}s",
        "fps": settings.video_fps,
        "resolution": settings.video_media_resolution,
        "prices": {
            "input_per_M": settings.price_input_per_million,
            "output_per_M": settings.price_output_per_million,
            "search_per_1k": settings.price_per_1k_search_queries,
        },
        "preflight": preflight,
    }
    out_dir = REPO / "eval" / "runs" / f"live-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(
        json.dumps({"meta": meta, "results": results}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (out_dir / "report.md").write_text(render_report(meta, results), encoding="utf-8")
    print(f"wrote {out_dir.relative_to(REPO)}/report.md", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
