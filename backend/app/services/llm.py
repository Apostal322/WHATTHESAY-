"""Model adapter layer.

Business logic depends only on the `LLMClient` protocol and the plain
dataclasses below. `GeminiClient` is the single place that knows about the
google-genai SDK; tests substitute a deterministic fake.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel

from app.core.errors import (
    AppError,
    InvalidModelOutputError,
    MissingCredentialsError,
    ModelNotFoundError,
    ProviderError,
    ProviderQuotaExhaustedError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    VideoUnavailableError,
)
from app.core.logging import redact_secrets
from app.services.search import SpendGuard

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class VideoClip:
    url: str
    start_sec: int
    end_sec: int
    fps: float
    media_resolution: str = "low"


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    video_tokens: int | None = None
    audio_tokens: int | None = None

    def __add__(self, other: Usage) -> Usage:
        def _opt(a: int | None, b: int | None) -> int | None:
            return None if a is None and b is None else (a or 0) + (b or 0)

        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            video_tokens=_opt(self.video_tokens, other.video_tokens),
            audio_tokens=_opt(self.audio_tokens, other.audio_tokens),
        )


@dataclass(frozen=True)
class LLMResult:
    text: str
    usage: Usage
    model: str


@dataclass(frozen=True)
class GroundingSource:
    """One retrieved web source as reported by search grounding metadata."""

    index: int
    uri: str
    title: str | None
    domain: str | None


@dataclass(frozen=True)
class GroundingSupportSpan:
    text: str
    source_indices: tuple[int, ...]


@dataclass(frozen=True)
class GroundedResult:
    text: str
    sources: list[GroundingSource]
    supports: list[GroundingSupportSpan]
    queries: list[str]
    usage: Usage
    model: str
    raw_metadata_present: bool = field(default=False)


class LLMClient(Protocol):
    async def generate_json(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        schema: type[BaseModel],
        video: VideoClip | None = None,
    ) -> LLMResult: ...

    async def grounded_search(self, *, model: str, system: str, prompt: str) -> GroundedResult: ...


# --------------------------------------------------------------------------
# Gemini implementation
# --------------------------------------------------------------------------

_RESOLUTION = {
    "low": types.MediaResolution.MEDIA_RESOLUTION_LOW,
    "medium": types.MediaResolution.MEDIA_RESOLUTION_MEDIUM,
    "high": types.MediaResolution.MEDIA_RESOLUTION_HIGH,
}
_RETRYABLE_STATUS = [429, 500, 502, 503, 504]


def _usage_from(resp: Any) -> Usage:
    meta = getattr(resp, "usage_metadata", None)
    if meta is None:
        return Usage()
    video = audio = None
    for detail in getattr(meta, "prompt_tokens_details", None) or []:
        modality = str(getattr(detail, "modality", "")).upper()
        count = getattr(detail, "token_count", None) or 0
        if "VIDEO" in modality:
            video = (video or 0) + count
        elif "AUDIO" in modality:
            audio = (audio or 0) + count
    prompt = meta.prompt_token_count or 0
    output = (meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0)
    return Usage(
        prompt_tokens=prompt + (meta.tool_use_prompt_token_count or 0),
        output_tokens=output,
        total_tokens=meta.total_token_count or 0,
        video_tokens=video,
        audio_tokens=audio,
    )


def _response_text(resp: Any) -> str:
    """Concatenate non-thought text parts; raise if the model returned nothing usable."""
    candidates = getattr(resp, "candidates", None) or []
    if not candidates:
        feedback = getattr(resp, "prompt_feedback", None)
        reason = getattr(feedback, "block_reason", None)
        raise InvalidModelOutputError(f"Model returned no candidates (block_reason={reason}).")
    content = getattr(candidates[0], "content", None)
    parts = getattr(content, "parts", None) or []
    text = "".join(
        p.text for p in parts if getattr(p, "text", None) and not getattr(p, "thought", False)
    )
    if not text.strip():
        finish = getattr(candidates[0], "finish_reason", None)
        raise InvalidModelOutputError(f"Model returned an empty response (finish_reason={finish}).")
    return text


def parse_grounding(
    resp: Any,
) -> tuple[list[GroundingSource], list[GroundingSupportSpan], list[str], bool]:
    """Convert SDK grounding metadata into plain dataclasses. Pure; unit-tested."""
    candidates = getattr(resp, "candidates", None) or []
    meta = getattr(candidates[0], "grounding_metadata", None) if candidates else None
    if meta is None:
        return [], [], [], False

    sources: list[GroundingSource] = []
    for i, chunk in enumerate(meta.grounding_chunks or []):
        web = getattr(chunk, "web", None)
        if web is None or not web.uri:
            continue
        sources.append(GroundingSource(index=i, uri=web.uri, title=web.title, domain=web.domain))

    valid = {s.index for s in sources}
    supports: list[GroundingSupportSpan] = []
    for sup in meta.grounding_supports or []:
        seg_text = (sup.segment.text if sup.segment else None) or ""
        idx = tuple(i for i in (sup.grounding_chunk_indices or []) if i in valid)
        if seg_text.strip() and idx:
            supports.append(GroundingSupportSpan(text=seg_text.strip(), source_indices=idx))

    return sources, supports, list(meta.web_search_queries or []), True


class GeminiClient:
    def __init__(
        self,
        api_key: str | None,
        *,
        timeout_sec: float,
        max_retries: int,
        transport: httpx.AsyncBaseTransport | None = None,
        fallback_model: str | None = None,
        daily_call_limit: int | None = None,
    ) -> None:
        # A missing key is reported on first use, so input validation errors surface first
        # and the server can start (and serve /health) without credentials.
        self._has_key = bool(api_key)
        # Used once per call, only when the requested model is overloaded (HTTP 503).
        self._fallback_model = fallback_model
        # Daily cap on model requests sent by this process (each fallback attempt counts;
        # SDK-internal retries are bounded by max_retries). Resets 00:00 UTC.
        self._call_budget = SpendGuard(daily_call_limit) if daily_call_limit else None
        self._timeout_sec = timeout_sec
        self._client = genai.Client(
            api_key=api_key or "missing",
            http_options=types.HttpOptions(
                timeout=int(timeout_sec * 1000),
                # Pin the transport to httpx (the SDK would otherwise prefer aiohttp when
                # installed) so network errors are classified consistently below.
                # `transport` is injectable for tests.
                httpx_async_client=httpx.AsyncClient(
                    transport=transport, timeout=httpx.Timeout(timeout_sec)
                ),
                retry_options=types.HttpRetryOptions(
                    attempts=max_retries + 1,
                    initial_delay=1.0,
                    max_delay=8.0,
                    http_status_codes=_RETRYABLE_STATUS,
                ),
            ),
        )

    async def _call(
        self,
        *,
        model: str,
        contents: Any,
        config: types.GenerateContentConfig,
        video: bool,
        operation: str,
    ) -> Any:
        if not self._has_key:
            raise MissingCredentialsError(
                "GEMINI_API_KEY is not set on the backend. Add it to .env and restart."
            )
        if self._call_budget is not None and not self._call_budget.try_reserve(1):
            log.warning("model_call_cap_reached", extra={"operation": operation, "model": model})
            raise ProviderQuotaExhaustedError(
                "The service's daily AI capacity has been reached. Try again tomorrow.",
                diagnostics={"operation": operation, "model": model, "reason": "daily_call_cap"},
            )
        try:
            # Outer guard: the SDK timeout is per attempt; retries can multiply it.
            return await asyncio.wait_for(
                self._client.aio.models.generate_content(
                    model=model, contents=contents, config=config
                ),
                timeout=self._timeout_sec * 2.5,
            )
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise ProviderTimeoutError(
                "The AI provider did not respond in time.",
                diagnostics={
                    "operation": operation,
                    "model": model,
                    "exception": type(exc).__name__,
                },
            ) from exc
        except genai_errors.APIError as exc:
            raise _classify_api_error(exc, model=model, video=video, operation=operation) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"Network error talking to the AI provider: {type(exc).__name__}",
                diagnostics={
                    "operation": operation,
                    "model": model,
                    "exception": type(exc).__name__,
                },
            ) from exc

    async def _call_with_fallback(self, *, model: str, **kw: Any) -> tuple[Any, str]:
        """Call `model`; if it is overloaded (503), try the fallback model once.

        Returns the response and the model that actually produced it. Quota (429),
        credential and input errors are never retried on another model.
        """
        try:
            return await self._call(model=model, **kw), model
        except AppError as exc:
            fallback = self._fallback_model
            overloaded = (exc.diagnostics or {}).get("http_status") == 503
            if not (overloaded and fallback and fallback != model):
                raise
            log.warning(
                "model_fallback",
                extra={"from_model": model, "to_model": fallback, "operation": kw["operation"]},
            )
            return await self._call(model=fallback, **kw), fallback

    async def generate_json(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        schema: type[BaseModel],
        video: VideoClip | None = None,
    ) -> LLMResult:
        parts: list[types.Part] = []
        config_kwargs: dict[str, Any] = {}
        if video is not None:
            parts.append(
                types.Part(
                    # Public YouTube URLs are passed as file_uri with no mime_type.
                    file_data=types.FileData(file_uri=video.url),
                    video_metadata=types.VideoMetadata(
                        start_offset=f"{video.start_sec}s",
                        end_offset=f"{video.end_sec}s",
                        fps=video.fps,
                    ),
                )
            )
            config_kwargs["media_resolution"] = _RESOLUTION[video.media_resolution]
        parts.append(types.Part(text=prompt))

        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
            **config_kwargs,
        )
        resp, used = await self._call_with_fallback(
            model=model,
            contents=[types.Content(role="user", parts=parts)],
            config=config,
            video=video is not None,
            operation="video_extraction" if video is not None else "structured_json",
        )
        return LLMResult(text=_response_text(resp), usage=_usage_from(resp), model=used)

    async def grounded_search(self, *, model: str, system: str, prompt: str) -> GroundedResult:
        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        )
        resp, used = await self._call_with_fallback(
            model=model, contents=prompt, config=config, video=False, operation="grounded_search"
        )
        sources, supports, queries, present = parse_grounding(resp)
        try:
            text = _response_text(resp)
        except InvalidModelOutputError:
            text = ""
        return GroundedResult(
            text=text,
            sources=sources,
            supports=supports,
            queries=queries,
            usage=_usage_from(resp),
            model=used,
            raw_metadata_present=present,
        )


def _provider_diagnostics(
    exc: genai_errors.APIError, *, model: str, operation: str
) -> dict[str, Any]:
    """Sanitized, operator-facing summary of a provider error (never sent to clients)."""
    diag: dict[str, Any] = {
        "operation": operation,
        "model": model,
        "http_status": getattr(exc, "code", None),
        "provider_status": getattr(exc, "status", None),
        "provider_message": redact_secrets((getattr(exc, "message", None) or str(exc))[:500]),
    }
    body = getattr(exc, "details", None)
    error = body.get("error", body) if isinstance(body, dict) else {}
    details = error.get("details") if isinstance(error, dict) else None
    for item in details if isinstance(details, list) else []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("@type", ""))
        if kind.endswith("QuotaFailure"):
            diag["quota_violations"] = [
                {
                    "metric": v.get("quotaMetric"),
                    "id": v.get("quotaId"),
                    "dimensions": v.get("quotaDimensions"),
                    "value": v.get("quotaValue"),
                }
                for v in item.get("violations", [])
                if isinstance(v, dict)
            ]
        elif kind.endswith("RetryInfo"):
            diag["retry_delay"] = str(item.get("retryDelay", ""))
    return diag


def _classify_api_error(
    exc: genai_errors.APIError, *, model: str, video: bool, operation: str = "unknown"
) -> AppError:
    diag = _provider_diagnostics(exc, model=model, operation=operation)
    error = _classify(exc, model=model, video=video, diag=diag)
    error.diagnostics = diag
    log.warning("provider_error", extra={"error_code": error.code, **diag})
    return error


# A 429 whose retry delay is at least this long is a used-up quota, not a burst limit.
_QUOTA_EXHAUSTED_MIN_DELAY_SEC = 3600


def _is_quota_exhausted(diag: dict[str, Any]) -> bool:
    violations = diag.get("quota_violations") or []
    if any("perday" in str(v.get("id", "")).lower() for v in violations):
        return True
    delay = str(diag.get("retry_delay", "")).removesuffix("s")
    if delay:
        try:
            return float(delay) >= _QUOTA_EXHAUSTED_MIN_DELAY_SEC
        except ValueError:
            return False
    # No machine-readable detail (seen live for Google Search grounding): a body that says
    # the quota is exceeded is treated as exhausted, so nothing retries it in a loop.
    message = str(diag.get("provider_message", "")).lower()
    return not violations and "exceeded your current quota" in message


def _classify(
    exc: genai_errors.APIError, *, model: str, video: bool, diag: dict[str, Any]
) -> AppError:
    code = getattr(exc, "code", None) or 0
    message = (getattr(exc, "message", None) or str(exc))[:300]
    lowered = message.lower()

    if code == 429 and _is_quota_exhausted(diag):
        return ProviderQuotaExhaustedError(
            "The Gemini API quota for this key has been exceeded (for example a free-tier daily "
            "or search-grounding limit). Try again later, or use a key with a higher quota."
        )
    if code == 429:
        return ProviderRateLimitedError(
            "The AI provider rate-limited the request. Try again shortly."
        )
    if code in (401,) or (code == 403 and "api key" in lowered) or "api key not valid" in lowered:
        return MissingCredentialsError("The Gemini API key was rejected. Check GEMINI_API_KEY.")
    if code == 404 and "model" in lowered:
        return ModelNotFoundError(
            f"Model '{model}' is not available. Run scripts/list_models.py and set "
            "YOUVERIFY_EXTRACTION_MODEL / YOUVERIFY_VERIFICATION_MODEL."
        )
    if (
        video
        and code in (400, 403, 404)
        and any(
            w in lowered
            for w in ("video", "youtube", "file", "uri", "url", "private", "unavailable")
        )
    ):
        return VideoUnavailableError(
            "Gemini could not access this video (it may be private, unlisted, age-restricted, "
            "region-locked, or too long). Enter the claim manually instead."
        )
    if code >= 500:
        return ProviderError("The AI provider had a server error. Try again.")
    return ProviderError(f"The AI provider rejected the request ({code}).")
