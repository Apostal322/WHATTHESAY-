"""Step A: timestamp-based claim extraction from a YouTube video."""

from __future__ import annotations

import logging
import time

from app.core.cache import TTLCache
from app.core.config import Settings
from app.core.singleflight import SingleFlight
from app.models.llm_outputs import ExtractionOutput
from app.models.schemas import ExtractRequest, ExtractResponse, TimeWindow, TokenUsage
from app.services.llm import LLMClient, VideoClip
from app.services.prompts import EXTRACTION_SYSTEM, extraction_prompt
from app.services.structured import clip, parse_model_json, to_token_usage
from app.services.youtube import canonical_watch_url, parse_video_id

log = logging.getLogger(__name__)

# Clicks within the same few seconds should hit the cache.
_CACHE_BUCKET_SEC = 3


def compute_window(
    timestamp_sec: float, duration_sec: float | None, before: int, after: int
) -> TimeWindow:
    t = int(timestamp_sec)
    start = max(0, t - before)
    end = t + after
    if duration_sec is not None:
        end = min(end, int(duration_sec))
    end = max(end, start + 1)
    return TimeWindow(start_sec=start, end_sec=end)


class ExtractionService:
    def __init__(
        self,
        llm: LLMClient,
        settings: Settings,
        cache: TTLCache[ExtractResponse] | None = None,
        flights: SingleFlight[ExtractResponse, None] | None = None,
    ) -> None:
        self._llm = llm
        self._settings = settings
        self._cache = cache
        self._flights = flights

    async def extract(self, req: ExtractRequest) -> ExtractResponse:
        s = self._settings
        video_id = parse_video_id(req.youtube_url, req.video_id)
        window = compute_window(
            req.timestamp_sec, req.duration_sec, s.window_before_sec, s.window_after_sec
        )

        bucket = int(req.timestamp_sec) // _CACHE_BUCKET_SEC
        cache_key = f"{video_id}:{bucket}:{req.language}:{s.extraction_model}"
        if self._cache is not None and (hit := self._cache.get(cache_key)) is not None:
            log.info("extract_cache_hit", extra={"video_id": video_id})
            return hit.model_copy(update={"cached": True, "latency_ms": 0})
        if self._flights is None:
            return await self._extract(req, video_id, window, cache_key)
        # A second click on the same moment while the first is running shares its model call.
        response, joined = await self._flights.run(
            cache_key, lambda _emit: self._extract(req, video_id, window, cache_key)
        )
        return response.model_copy(update={"cached": True}) if joined else response

    async def _extract(
        self, req: ExtractRequest, video_id: str, window: TimeWindow, cache_key: str
    ) -> ExtractResponse:
        s = self._settings
        started = time.perf_counter()
        result = await self._llm.generate_json(
            model=s.extraction_model,
            system=EXTRACTION_SYSTEM,
            prompt=extraction_prompt(
                timestamp_sec=req.timestamp_sec,
                start_sec=window.start_sec,
                end_sec=window.end_sec,
                language=req.language,
            ),
            schema=ExtractionOutput,
            video=VideoClip(
                url=canonical_watch_url(video_id),
                start_sec=window.start_sec,
                end_sec=window.end_sec,
                fps=s.video_fps,
                media_resolution=s.video_media_resolution,
            ),
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        out = parse_model_json(result.text, ExtractionOutput)
        response = build_extract_response(
            out,
            video_id,
            req.timestamp_sec,
            window,
            result.model,
            latency_ms,
            to_token_usage(result.usage, s),
        )

        log.info(
            "claim_extracted",
            extra={
                "video_id": video_id,
                "status": response.status,
                "claim_type": response.claim_type,
                "latency_ms": latency_ms,
                "prompt_tokens": result.usage.prompt_tokens,
                "window_sec": window.end_sec - window.start_sec,
            },
        )
        if self._cache is not None:
            self._cache.set(cache_key, response)
        return response


def build_extract_response(
    out: ExtractionOutput,
    video_id: str,
    timestamp_sec: float,
    window: TimeWindow,
    model: str,
    latency_ms: int,
    usage: TokenUsage,
) -> ExtractResponse:
    """Normalise model output into the API contract, failing closed on inconsistencies."""
    claim = clip(out.claim, 1000)
    found = out.found and claim is not None
    evidence = clip(out.evidence_from_video, 1500)
    # A quote flag without quote text is meaningless; never claim verbatim by default.
    is_exact = bool(out.is_exact_quote and evidence)

    if not found:
        return ExtractResponse(
            status="no_claim_found",
            video_id=video_id,
            timestamp_sec=int(timestamp_sec),
            window=window,
            claim=None,
            claim_type=None,
            context=clip(out.context, 600),
            evidence_from_video=None,
            is_exact_quote=False,
            language=out.language,
            reason=clip(out.reason_if_not_found, 400)
            or "No clear checkable statement was found near this timestamp.",
            model=model,
            latency_ms=latency_ms,
            usage=usage,
        )

    return ExtractResponse(
        status="success",
        video_id=video_id,
        timestamp_sec=int(timestamp_sec),
        window=window,
        claim=claim,
        claim_type=out.claim_type or "ambiguous",
        context=clip(out.context, 600),
        evidence_from_video=evidence,
        is_exact_quote=is_exact,
        language=clip(out.language, 10),
        model=model,
        latency_ms=latency_ms,
        usage=usage,
    )
