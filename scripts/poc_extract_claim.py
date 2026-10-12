#!/usr/bin/env python3
"""Phase 1 feasibility PoC: YouTube URL + timestamp -> AI-extracted claim -> JSON.

Usage (from repo root, with the backend venv active and GEMINI_API_KEY set):

    python scripts/poc_extract_claim.py \\
        --url "https://www.youtube.com/watch?v=VIDEO_ID" --timestamp 755
    echo '{"youtube_url": "...", "timestamp_sec": 755, "language": "ru"}' \\
        | python scripts/poc_extract_claim.py --stdin
    python scripts/poc_extract_claim.py --url ... --timestamp 755 --probe-clipping

--probe-clipping answers the key cost question empirically: does Gemini honour
start/end offsets for YouTube URLs, or does it process the whole video? It runs
the same request with a short and a 3x longer window and compares the reported
prompt tokens. Proportional growth => clipping honoured; flat => ignored.

This uses the same ExtractionService as the API, so what the PoC measures is
what the product does.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.config import get_settings
from app.core.errors import AppError
from app.models.llm_outputs import ExtractionOutput
from app.models.schemas import ExtractRequest
from app.services.extraction import ExtractionService
from app.services.llm import GeminiClient, VideoClip
from app.services.prompts import EXTRACTION_SYSTEM, extraction_prompt
from app.services.youtube import canonical_watch_url, parse_video_id


def _client() -> GeminiClient:
    s = get_settings()
    key = s.gemini_api_key.get_secret_value() if s.gemini_api_key else None
    return GeminiClient(key, timeout_sec=s.request_timeout_sec, max_retries=s.max_retries)


async def run_extraction(payload: dict[str, object]) -> dict[str, object]:
    settings = get_settings()
    req = ExtractRequest.model_validate(payload)
    service = ExtractionService(_client(), settings, cache=None)
    started = time.perf_counter()
    resp = await service.extract(req)
    wall_ms = int((time.perf_counter() - started) * 1000)
    clip_len = resp.window.end_sec - resp.window.start_sec
    return {
        # Spec-shaped result
        "status": resp.status,
        "claim": resp.claim,
        "claim_type": resp.claim_type,
        "timestamp_sec": resp.timestamp_sec,
        "evidence_from_video": resp.evidence_from_video,
        "is_exact_quote": resp.is_exact_quote,
        "requires_user_confirmation": resp.requires_user_confirmation,
        # Diagnostics
        "context": resp.context,
        "language": resp.language,
        "reason": resp.reason,
        "diagnostics": {
            "model": resp.model,
            "window": resp.window.model_dump(),
            "latency_ms": wall_ms,
            "usage": resp.usage.model_dump(),
            "prompt_tokens_per_clip_second": round(resp.usage.prompt_tokens / clip_len, 1)
            if clip_len
            else None,
        },
    }


async def probe_clipping(payload: dict[str, object]) -> dict[str, object]:
    settings = get_settings()
    req = ExtractRequest.model_validate(payload)
    video_id = parse_video_id(req.youtube_url, req.video_id)
    client = _client()
    t = int(req.timestamp_sec)
    results = []
    for before in (15, 45):
        start, end = max(0, t - before), t + 5
        began = time.perf_counter()
        res = await client.generate_json(
            model=settings.extraction_model,
            system=EXTRACTION_SYSTEM,
            prompt=extraction_prompt(
                timestamp_sec=t, start_sec=start, end_sec=end, language=req.language
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
        results.append(
            {
                "window_sec": end - start,
                "prompt_tokens": res.usage.prompt_tokens,
                "video_tokens": res.usage.video_tokens,
                "audio_tokens": res.usage.audio_tokens,
                "latency_ms": int((time.perf_counter() - began) * 1000),
            }
        )
    short, long_ = results
    window_ratio = long_["window_sec"] / short["window_sec"]
    token_ratio = long_["prompt_tokens"] / max(1, short["prompt_tokens"])
    if token_ratio > 1 + (window_ratio - 1) * 0.5:
        verdict = "clipping_honoured"
    elif token_ratio < 1.15:
        verdict = "clipping_ignored_or_capped"
    else:
        verdict = "inconclusive"
    return {
        "runs": results,
        "window_ratio": round(window_ratio, 2),
        "prompt_token_ratio": round(token_ratio, 2),
        "conclusion": verdict,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--url", help="Public YouTube watch URL")
    ap.add_argument("--timestamp", type=float, help="Playback position in seconds")
    ap.add_argument("--language", default="auto", choices=["auto", "en", "ru"])
    ap.add_argument("--stdin", action="store_true", help="Read the JSON request from stdin")
    ap.add_argument(
        "--probe-clipping", action="store_true", help="Measure whether offsets are honoured"
    )
    args = ap.parse_args()

    if args.stdin:
        payload = json.load(sys.stdin)
    elif args.url and args.timestamp is not None:
        payload = {
            "youtube_url": args.url,
            "timestamp_sec": args.timestamp,
            "language": args.language,
        }
    else:
        ap.error("Provide --url and --timestamp, or --stdin.")

    try:
        coro = probe_clipping(payload) if args.probe_clipping else run_extraction(payload)
        out = asyncio.run(coro)
    except AppError as exc:
        out = {
            "status": "error",
            "code": exc.code,
            "message": exc.message,
            "retryable": exc.retryable,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 1
    except ValueError as exc:  # request validation
        print(
            json.dumps({"status": "error", "code": "invalid_input", "message": str(exc)}, indent=2)
        )
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
