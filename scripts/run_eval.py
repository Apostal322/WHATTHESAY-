#!/usr/bin/env python3
"""Run the evaluation protocol (docs/evaluation.md) against the live Gemini API.

    python scripts/run_eval.py eval/dataset.example.jsonl
    python scripts/run_eval.py eval/my_videos.jsonl --verify-from extracted

Items with video_id+timestamp_sec are run through extraction; every item is run
through verification (on the ground-truth claim by default, or on the extracted
claim with --verify-from extracted to measure the end-to-end pipeline).

Results go to eval/runs/<timestamp>.jsonl plus a printed summary. This costs
API credits; nothing here is mocked. Results are only as good as the labels.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

import httpx

from app.core.config import get_settings
from app.core.errors import AppError
from app.evaluation.metrics import EvalItem, EvalRecord, domain_recall, summarize, token_f1
from app.models.schemas import ExtractRequest, VerifyRequest
from app.services.citations import is_safe_display_url
from app.services.extraction import ExtractionService
from app.services.llm import GeminiClient
from app.services.verification import VerificationService


async def run_item(
    item: EvalItem,
    extraction: ExtractionService,
    verification: VerificationService,
    verify_from: str,
) -> EvalRecord:
    rec = EvalRecord(id=item.id)
    claim_to_verify: str | None = item.ground_truth_claim
    try:
        if item.video_id and item.timestamp_sec is not None:
            ext = await extraction.extract(
                ExtractRequest(
                    video_id=item.video_id, timestamp_sec=item.timestamp_sec, language=item.language
                )
            )
            rec.extraction_status = ext.status
            rec.extracted_claim = ext.claim
            rec.extracted_claim_type = ext.claim_type
            rec.claim_f1 = round(token_f1(ext.claim or "", item.ground_truth_claim), 4)
            rec.extraction_latency_ms = ext.latency_ms
            rec.total_tokens += ext.usage.total_tokens
            if verify_from == "extracted":
                claim_to_verify = ext.claim

        if claim_to_verify:
            started = time.perf_counter()
            ver = await verification.verify(
                VerifyRequest(claim=claim_to_verify, language=item.language)
            )
            rec.verification_latency_ms = int((time.perf_counter() - started) * 1000)
            rec.predicted_verdict = ver.verdict
            rec.source_urls = [s.url for s in ver.sources]
            rec.valid_citation_count = sum(is_safe_display_url(s.url) for s in ver.sources)
            rec.relevant_source_count = sum(
                1 for s in ver.sources if s.stance not in (None, "irrelevant")
            )
            rec.reference_domain_recall = domain_recall(
                rec.source_urls, [e.url for e in item.supporting_evidence]
            )
            rec.total_tokens += ver.usage.total_tokens
            rec.estimated_cost_usd = ver.usage.estimated_cost_usd
    except AppError as exc:
        rec.error = f"{exc.code}: {exc.message}"
    return rec


async def run_all(items: list[EvalItem], verify_from: str) -> list[EvalRecord] | None:
    settings = get_settings().model_copy(update={"cache_ttl_sec": 0})
    key = settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else None
    llm = GeminiClient(
        key, timeout_sec=settings.request_timeout_sec, max_retries=settings.max_retries
    )

    records: list[EvalRecord] = []
    async with httpx.AsyncClient(timeout=5.0) as http:
        extraction = ExtractionService(llm, settings)
        verification = VerificationService(llm, settings, http)
        for item in items:
            rec = await run_item(item, extraction, verification, verify_from)
            records.append(rec)
            status = rec.error or rec.predicted_verdict
            print(f"{item.id:28s} ref={item.reference_verdict:22s} got={status}", file=sys.stderr)
            if rec.error and rec.error.startswith("missing_credentials"):
                print("GEMINI_API_KEY is not set; aborting.", file=sys.stderr)
                return None
    return records


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("dataset", help="Path to a JSONL dataset (see eval/dataset.example.jsonl)")
    ap.add_argument("--verify-from", choices=["ground_truth", "extracted"], default="ground_truth")
    ap.add_argument("--claim-match-threshold", type=float, default=0.5)
    args = ap.parse_args()

    lines = Path(args.dataset).read_text(encoding="utf-8").splitlines()
    items = [EvalItem.model_validate(json.loads(line)) for line in lines if line.strip()]
    records = asyncio.run(run_all(items, args.verify_from))
    if records is None:
        return 1

    out_dir = REPO / "eval" / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}.jsonl"
    out.write_text("\n".join(r.model_dump_json() for r in records) + "\n", encoding="utf-8")

    settings = get_settings()
    summary = summarize(items, records, args.claim_match_threshold)
    summary["run_file"] = str(out.relative_to(REPO))
    summary["models"] = {
        "extraction": settings.extraction_model,
        "verification": settings.verification_model,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
