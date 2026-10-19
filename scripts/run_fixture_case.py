#!/usr/bin/env python3
"""Verify a reference case proposition by proposition over its OFFLINE evidence fixture.

Retrieval is replaced by the captured excerpts (eval/fixtures/<case>/fixture.json); analysis,
evaluation and all guardrails are the real pipeline. With `--llm gemini` the evaluator is the
live model: the result is "live evaluation of offline evidence", NOT a live fact-check, and the
artifact says so.

    python scripts/run_fixture_case.py                       # dry run: plan + call count, no calls
    python scripts/run_fixture_case.py --llm gemini --max-calls 10
    python scripts/run_fixture_case.py --llm gemini --max-calls 4 --only P2 P5

Calls: up to 3 per proposition (analysis, evaluation, and the entailment check for claims about
what someone said); no search or grounding call is made.
YOUVERIFY_MAX_RETRIES is forced to 0 and the fallback model is disabled unless
--allow-fallback, so --max-calls is a hard bound. Refuses to start if the fixture has no
captured excerpts (run scripts/capture_evidence.py first). Writes
eval/factchecks/<case>-fixture-<timestamp>.json and .md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.evaluation.chronology import on_record_by
from app.evaluation.fixture import EvidenceFixture, FixtureRetriever
from app.evaluation.propositions import PropositionResult, verify_propositions
from app.evaluation.reference import ReferenceRecord
from app.services.llm import GeminiClient
from app.services.verification import VerificationService

# Analysis and evaluation, plus the entailment check when a claim about what someone said gets
# a confident verdict: an upper bound, so the run never stops part-way through a proposition.
CALLS_PER_PROPOSITION = 3


def load_case(case: str) -> tuple[ReferenceRecord, EvidenceFixture]:
    ref = ReferenceRecord.model_validate(
        json.loads((REPO / "eval" / "reference" / f"{case}.json").read_text(encoding="utf-8"))
    )
    fixture = EvidenceFixture.model_validate(
        json.loads((REPO / "eval" / "fixtures" / case / "fixture.json").read_text(encoding="utf-8"))
    )
    return ref, fixture


def plan(ref: ReferenceRecord, fixture: EvidenceFixture, only: list[str] | None) -> dict[str, Any]:
    props = [p for p in ref.propositions if not only or p.id in only]
    return {
        "propositions": [p.id for p in props],
        "model_calls": CALLS_PER_PROPOSITION * len(props),
        "captured_sources": [s.id for s in fixture.captured()],
        "pending_capture": [s.id for s in fixture.sources if s.status == "pending_capture"],
    }


def render_markdown(a: dict[str, Any]) -> str:
    lines = [
        f"# Proposition run: {a['case_id']} ({a['mode']})",
        "",
        f"> {a['label']}",
        "",
        f"- Run at {a['run_at']} · evaluator `{a['model']}` · reference review status "
        f"**{a['reference_review_status']}** (scores are provisional unless human_verified)",
        f"- Evidence: captured sources {a['plan']['captured_sources']}; pending "
        f"{a['plan']['pending_capture']}",
        "",
        "| prop | kind | verdict | reference (acceptable) | outcome | guardrail notes |",
        "|---|---|---|---|---|---|",
    ]
    for r in a["results"]:
        v = r["verification"] or {}
        s = r["reference_score"]
        notes = "<br>".join(v.get("guardrail_notes") or []).replace("|", "\\|")
        lines.append(
            f"| {r['proposition_id']} | {r['kind']} | {v.get('verdict') or r['error']} | "
            f"{r['reference']} | {s['outcome']} | {notes} |"
        )
    for r in a["results"]:
        if r["chronology"]:
            c = r["chronology"]
            lines += [
                "",
                f"Chronology {r['proposition_id']}: {c['relation']} {c['from']} → {c['to']} "
                f"({c['days']} day(s)): **{c['status']}**; {c['caveats']}",
            ]
    return "\n".join(lines) + "\n"


async def run(args: argparse.Namespace) -> int:
    ref, fixture = load_case(args.case)
    p = plan(ref, fixture, args.only)
    print(json.dumps(p, indent=2))
    if args.llm == "none":
        print("dry run: no model calls made. Use --llm gemini --max-calls N to run.")
        return 0
    if not p["captured_sources"]:
        raise SystemExit("no captured excerpts in the fixture: run scripts/capture_evidence.py")
    if args.max_calls is None or p["model_calls"] > args.max_calls:
        raise SystemExit(f"this run needs {p['model_calls']} calls; pass --max-calls >= that")
    settings = get_settings().model_copy(
        update={
            "cache_ttl_sec": 0,
            "max_retries": 0,
            "fallback_model": settings_fallback(args),
        }
    )
    key = settings.gemini_api_key
    if not key:
        raise SystemExit("GEMINI_API_KEY is not set")
    configure_logging("WARNING")
    llm = GeminiClient(
        key.get_secret_value(),
        timeout_sec=settings.request_timeout_sec,
        max_retries=0,
        fallback_model=settings.fallback_model,
        daily_call_limit=args.max_calls,
    )
    service = VerificationService(llm, settings, retriever=FixtureRetriever(fixture))
    results: list[PropositionResult] = await verify_propositions(service, ref, only=args.only)
    split = on_record_by(ref)
    artifact = {
        "case_id": ref.id,
        "mode": "live_evaluator_offline_evidence",
        "label": (
            "Live model evaluation of OFFLINE fixture evidence. Not a live fact-check: no "
            "search was run, and the evidence set was chosen in advance."
        ),
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": settings.verification_model,
        "reference_review_status": ref.review.status,
        "plan": p,
        "events_after_claim_date": [e.id for e in split["after_claim"]],
        "results": [
            {
                **r.to_dict(),
                "reference": f"{ref.proposition(r.proposition_id).reference_verdict} "
                f"({', '.join(ref.proposition(r.proposition_id).acceptable_verdicts)})",
            }
            for r in results
        ],
    }
    out = REPO / "eval" / "factchecks"
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{ref.id}-fixture-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    (out / f"{stem}.json").write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out / f"{stem}.md").write_text(render_markdown(artifact), encoding="utf-8")
    print(f"wrote eval/factchecks/{stem}.json and .md", file=sys.stderr)
    return 0 if all(r.error is None for r in results) else 1


def settings_fallback(args: argparse.Namespace) -> str | None:
    return get_settings().fallback_model if args.allow_fallback else None


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--case", default="en-debate-1200")
    ap.add_argument("--llm", choices=["none", "gemini"], default="none")
    ap.add_argument("--max-calls", type=int, help="hard bound on Gemini requests for this run")
    ap.add_argument("--only", nargs="+", help="proposition ids, e.g. P2 P5")
    ap.add_argument("--allow-fallback", action="store_true", help="allow one 503 fallback per call")
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
