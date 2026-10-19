#!/usr/bin/env python3
"""Verify one claim end to end (analysis -> retrieval -> evaluation) and save a review artifact.

The claim comes from a saved extraction (`--from-run eval/runs/<run>/results.json --case ID`),
so no extraction call is repeated, or is typed manually (`--claim ... [--context ...]`).
Manual input is labelled as such in the artifact and is never reported as a video extraction.

    python scripts/verify_claim.py --from-run eval/runs/live-X/results.json --case en-debate-1200 \\
        --price-in 0.75 --price-out 3.75 --price-search-1k 14

Model calls: 3 per run when nothing fails (analysis, retrieval, evaluation), 4 for a claim about
what someone said that reaches a confident verdict (entailment check); each step may add one
fallback call on HTTP 503 when YOUVERIFY_FALLBACK_MODEL is set. SDK retries follow
YOUVERIFY_MAX_RETRIES (set it to 0 to bound calls exactly). Writes
eval/factchecks/<id>-<timestamp>.json, plus a Markdown review sheet next to it.
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

from app.api.deps import build_search_retriever
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.evaluation.reference import ReferenceRecord, score_verdict
from app.models.schemas import VerifyRequest, VerifyResponse
from app.services.llm import GeminiClient, LLMClient
from app.services.verification import VerificationService

OUT_DIR = REPO / "eval" / "factchecks"
REFERENCE_DIR = REPO / "eval" / "reference"


def reference_score(
    case_id: str, verdict: str | None, ref_dir: Path = REFERENCE_DIR
) -> dict[str, Any] | None:
    """Score against eval/reference/<id>.json when one exists (provisional unless human-verified)."""
    path = ref_dir / f"{case_id}.json"
    if not path.exists():
        return None
    ref = ReferenceRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))
    return {
        **score_verdict(verdict, ref),  # type: ignore[arg-type]
        "reference_verdict": ref.reference_verdict,
        "acceptable_verdicts": ref.acceptable_verdicts,
        "reference_review_status": ref.review.status,
    }


def load_input(args: argparse.Namespace) -> dict[str, Any]:
    if args.from_run:
        data = json.loads(Path(args.from_run).read_text(encoding="utf-8"))
        if "input_method" in data:  # a committed fact-check artifact: reuse its input as is
            keys = ("id", "input_method", "claim", "context", "language", "video", "extraction")
            return {k: data.get(k) for k in keys}
        row = next(r for r in data["results"] if r["case"]["id"] == args.case)
        ext = row.get("extraction") or {}
        if ext.get("status") != "success" or not ext.get("claim"):
            raise SystemExit(f"{args.case}: the saved run has no successful extraction")
        case = row["case"]
        return {
            "id": case["id"],
            "input_method": "video_extraction",
            "claim": ext["claim"],
            "context": ext.get("context"),
            "language": case.get("language", "auto"),
            "video": {
                "url": case["youtube_url"],
                "timestamp_sec": case["timestamp_sec"],
                "window": ext.get("window"),
            },
            "extraction": {
                "run": str(Path(args.from_run).parent.name),
                "model": ext.get("model"),
                "claim_type": ext.get("claim_type"),
                "evidence_from_video": ext.get("evidence_from_video"),
                "is_exact_quote_model_reported": ext.get("is_exact_quote"),
                "quote_verified": False,  # nothing here checks the words against the video
                "speaker_verified": False,
                "latency_ms": ext.get("latency_ms"),
                "usage": ext.get("usage"),
            },
        }
    if not args.claim:
        raise SystemExit("give --from-run/--case or --claim")
    return {
        "id": args.id or "manual",
        "input_method": "manual",
        "claim": args.claim,
        "context": args.context,
        "language": args.language,
        "video": None,
        "extraction": None,
    }


def artifact(
    item: dict[str, Any],
    resp: VerifyResponse | None,
    error: AppError | None,
    latency_ms: int,
    meta: dict[str, Any],
) -> dict[str, Any]:
    out: dict[str, Any] = {**item, "run_meta": meta, "latency_ms": latency_ms}
    if error is not None:
        out["verification"] = {
            "error": error.code,
            "message": error.message,
            "diagnostics": error.diagnostics,
        }
    elif resp is not None:
        out["verification"] = resp.model_dump()
    verdict = resp.verdict if resp is not None else None
    out["reference_score"] = reference_score(item["id"], verdict)
    out["review"] = {
        "status": "unreviewed",
        "reviewer": None,
        "quote_checked_against_video_or_transcript": None,
        "speaker_checked": None,
        "sources_opened": None,
        "verdict_agrees": None,
        "notes": None,
    }
    return out


def render_markdown(a: dict[str, Any]) -> str:
    v = a.get("verification") or {}
    ext = a.get("extraction") or {}
    lines = [
        f"# Fact-check review sheet: {a['id']}",
        "",
        "Everything below is **model output or retrieved material** unless a review field says "
        "otherwise. Fill in the review section by hand.",
        "",
        "## Input",
        f"- Input method: **{a['input_method']}**",
    ]
    if a.get("video"):
        lines.append(
            f"- Video: {a['video']['url']} at {a['video']['timestamp_sec']} s "
            f"(window {a['video'].get('window')})"
        )
    lines += [f"- Claim checked: {a['claim']}", f"- Context (model-written): {a.get('context')}"]
    if ext:
        lines += [
            f"- Heard in video (model transcription; exact-quote flag "
            f"{ext.get('is_exact_quote_model_reported')} is **model-reported, unverified**): "
            f'"{ext.get("evidence_from_video")}"',
            f"- Extraction model: `{ext.get('model')}` · {ext.get('latency_ms')} ms · "
            f"usage {ext.get('usage')}",
        ]
    lines += ["", "## Result"]
    if "error" in v:
        diag = dict(v.get("diagnostics") or {})
        done = diag.pop("completed", None)
        lines += [
            f"**No verdict: {v['error']}** ({v.get('message')})",
            "",
            f"Diagnostics: `{json.dumps(diag, ensure_ascii=False)}`",
        ]
        if done:
            lines += [
                "",
                "Completed before the failure (model output, unreviewed):",
                f"```json\n{json.dumps(done, indent=2, ensure_ascii=False)}\n```",
            ]
    else:
        lines += [
            f"- Verdict: **{v.get('verdict')}** · claim type: {v.get('claim_type')}",
            f"- Normalized claim: {v.get('normalized_claim')}",
            f"- Explanation (model): {v.get('explanation')}",
            f"- Search queries executed: {v.get('search_queries')}",
            f"- Independent origins: {v.get('independent_source_count')}",
            f"- Guardrail notes: {v.get('guardrail_notes')}",
            f"- Limitations (model): {v.get('limitations')}",
            f"- Models: {v.get('models_used')} · verification latency {v.get('latency_ms')} ms · "
            f"usage {v.get('usage')}",
            "",
            "### Key findings (model)",
        ]
        lines += [
            f"- [{f['stance']}] {f['text']} ({', '.join(f['source_ids'])})"
            for f in v.get("key_findings") or []
        ] or ["- (none)"]
        lines += [
            "",
            "### Sources",
            "",
            "| id | stance (model) | domain | excerpt origin | url | excerpt | opened? | supports the stated stance? |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for s in v.get("sources") or []:
            ex = " / ".join(s.get("excerpts") or []).replace("|", "\\|")
            lines.append(
                f"| {s['id']} | {s.get('stance')} | {s.get('domain')} | "
                f"{s.get('excerpt_origin')} | {s['url']} | {ex} | | |"
            )
    rs = a.get("reference_score")
    if rs:
        counted = "counted" if rs["counted"] else "PROVISIONAL: reference is not human-verified"
        lines += [
            "",
            "## Against the reference record",
            f"- Reference verdict: {rs['reference_verdict']} (acceptable: {rs['acceptable_verdicts']}; "
            f"review status {rs['reference_review_status']})",
            f"- Outcome: {rs['outcome']} · false confident: {rs.get('false_confident')} · {counted}",
        ]
    lines += ["", "## Review (by hand)", ""]
    lines += [f"- {k}: " for k in a["review"]]
    return "\n".join(lines) + "\n"


async def run(
    item: dict[str, Any], settings: Settings, llm: LLMClient | None = None
) -> tuple[VerifyResponse | None, AppError | None, int]:
    if llm is None:
        key = settings.gemini_api_key
        if not key:
            raise SystemExit("GEMINI_API_KEY is not set")
        llm = GeminiClient(
            key.get_secret_value(),
            timeout_sec=settings.request_timeout_sec,
            max_retries=settings.max_retries,
            fallback_model=settings.fallback_model,
            daily_call_limit=settings.gemini_daily_call_limit,
        )
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as http:
        retriever = (
            build_search_retriever(settings, http)
            if settings.retrieval_backend == "search"
            else None
        )
        svc = VerificationService(llm, settings, http, retriever=retriever)
        started = time.perf_counter()
        try:
            resp = await svc.verify(
                VerifyRequest(
                    claim=item["claim"],
                    context=item.get("context"),
                    language=item.get("language", "auto"),
                )
            )
            return resp, None, int((time.perf_counter() - started) * 1000)
        except AppError as exc:
            return None, exc, int((time.perf_counter() - started) * 1000)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--from-run",
        help="results.json of a live_validation run, or an eval/factchecks/*.json artifact",
    )
    ap.add_argument("--case", help="case id inside --from-run")
    ap.add_argument("--claim", help="manual claim text")
    ap.add_argument("--context", help="manual context (speaker, date, event)")
    ap.add_argument("--language", default="auto", choices=["auto", "en", "ru"])
    ap.add_argument("--id", help="id for a manual claim")
    ap.add_argument("--price-in", type=float)
    ap.add_argument("--price-out", type=float)
    ap.add_argument("--price-search-1k", type=float)
    args = ap.parse_args()

    item = load_input(args)
    updates: dict[str, Any] = {"cache_ttl_sec": 0}
    for flag, field in (
        ("price_in", "price_input_per_million"),
        ("price_out", "price_output_per_million"),
        ("price_search_1k", "price_per_1k_search_queries"),
    ):
        if getattr(args, flag) is not None:
            updates[field] = getattr(args, flag)
    settings = get_settings().model_copy(update=updates)
    configure_logging("WARNING")

    resp, error, latency = asyncio.run(run(item, settings))
    meta = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "verification_model": settings.verification_model,
        "fallback_model": settings.fallback_model,
        "retrieval_backend": settings.retrieval_backend,
        "max_retries": settings.max_retries,
        "prices": {
            "input_per_M": settings.price_input_per_million,
            "output_per_M": settings.price_output_per_million,
            "search_per_1k": settings.price_per_1k_search_queries,
        },
    }
    a = artifact(item, resp, error, latency, meta)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"{item['id']}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    (OUT_DIR / f"{stem}.json").write_text(
        json.dumps(a, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (OUT_DIR / f"{stem}.md").write_text(render_markdown(a), encoding="utf-8")
    print(f"wrote {OUT_DIR.relative_to(REPO)}/{stem}.json and .md", file=sys.stderr)
    return 0 if error is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
