from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app import __version__
from app.api.deps import (
    get_extraction_service,
    get_state,
    get_verification_service,
    rate_limit,
)
from app.core.errors import AppError
from app.models.schemas import (
    ErrorResponse,
    ExtractRequest,
    ExtractResponse,
    HealthResponse,
    VerifyRequest,
    VerifyResponse,
)
from app.services.extraction import ExtractionService
from app.services.verification import Stage, VerificationService

log = logging.getLogger(__name__)
router = APIRouter()

_ERRORS: dict[int | str, dict[str, object]] = {
    code: {"model": ErrorResponse} for code in (422, 429, 502, 503, 504)
}


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    s = get_state(request).settings
    return HealthResponse(
        status="ok",
        version=__version__,
        gemini_configured=s.gemini_api_key is not None,
        extraction_model=s.extraction_model,
        verification_model=s.verification_model,
    )


@router.post(
    "/v1/claims/extract",
    response_model=ExtractResponse,
    responses=_ERRORS,
    dependencies=[Depends(rate_limit)],
)
async def extract_claim(
    body: ExtractRequest, service: Annotated[ExtractionService, Depends(get_extraction_service)]
) -> ExtractResponse:
    return await service.extract(body)


@router.post(
    "/v1/claims/verify",
    response_model=VerifyResponse,
    responses=_ERRORS,
    dependencies=[Depends(rate_limit)],
)
async def verify_claim(
    body: VerifyRequest,
    service: Annotated[VerificationService, Depends(get_verification_service)],
) -> VerifyResponse:
    return await service.verify(body)


@router.post(
    "/v1/claims/verify/stream",
    responses=_ERRORS,
    dependencies=[Depends(rate_limit)],
    response_class=StreamingResponse,
)
async def verify_claim_stream(
    body: VerifyRequest,
    service: Annotated[VerificationService, Depends(get_verification_service)],
) -> StreamingResponse:
    """Same as /verify, but emits NDJSON progress events so the UI can show real stages.

    Events: {"event":"stage","stage":...} then exactly one of
    {"event":"result","data":VerifyResponse} or {"event":"error","error":ErrorBody}.
    """
    queue: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()

    async def progress(stage: Stage) -> None:
        await queue.put({"event": "stage", "stage": stage})

    async def run() -> None:
        try:
            result = await service.verify(body, progress)
            await queue.put({"event": "result", "data": result.model_dump(mode="json")})
        except AppError as exc:
            await queue.put(
                {
                    "event": "error",
                    "error": {"code": exc.code, "message": exc.message, "retryable": exc.retryable},
                }
            )
        except Exception:
            log.exception("verify_stream_failed")
            await queue.put(
                {
                    "event": "error",
                    "error": {
                        "code": "internal_error",
                        "message": "Unexpected server error.",
                        "retryable": False,
                    },
                }
            )
        finally:
            await queue.put(None)

    async def events() -> AsyncIterator[bytes]:
        task = asyncio.create_task(run())
        try:
            while (item := await queue.get()) is not None:
                yield (json.dumps(item, ensure_ascii=False) + "\n").encode()
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        events(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store"}
    )
