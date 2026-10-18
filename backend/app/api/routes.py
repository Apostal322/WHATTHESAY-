from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable
from typing import Annotated, TypeVar

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app import __version__
from app.api.deps import (
    get_extraction_service,
    get_state,
    get_verification_service,
    rate_limit,
)
from app.core.errors import AppError, ClientDisconnectedError
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
T = TypeVar("T")
DISCONNECT_POLL_SEC = 0.5


async def cancel_on_disconnect(request: Request, work: Awaitable[T]) -> T:
    """Run `work`, cancelling it if the client goes away (e.g. the user pressed Cancel).

    Without this a long model call keeps running, and is billed, after nobody can see its
    result. The streaming endpoint gets the same behaviour from Starlette.
    """
    task = asyncio.ensure_future(work)

    async def watch() -> None:
        while not task.done():
            if await request.is_disconnected():
                task.cancel()
                return
            await asyncio.sleep(DISCONNECT_POLL_SEC)

    watcher = asyncio.create_task(watch())
    try:
        return await task
    except asyncio.CancelledError:
        if task.cancelled() and not watcher.cancelled() and watcher.done():
            log.info("client_disconnected", extra={"path": request.url.path})
            raise ClientDisconnectedError("The client closed the request.") from None
        raise
    finally:
        watcher.cancel()


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    s = get_state(request).settings
    return HealthResponse(
        status="ok",
        version=__version__,
        gemini_configured=s.gemini_api_key is not None,
        extraction_model=s.extraction_model,
        verification_model=s.verification_model,
        retrieval_backend=s.retrieval_backend,
    )


@router.post(
    "/v1/claims/extract",
    response_model=ExtractResponse,
    responses=_ERRORS,
    dependencies=[Depends(rate_limit)],
)
async def extract_claim(
    body: ExtractRequest,
    request: Request,
    service: Annotated[ExtractionService, Depends(get_extraction_service)],
) -> ExtractResponse:
    return await cancel_on_disconnect(request, service.extract(body))


@router.post(
    "/v1/claims/verify",
    response_model=VerifyResponse,
    responses=_ERRORS,
    dependencies=[Depends(rate_limit)],
)
async def verify_claim(
    body: VerifyRequest,
    request: Request,
    service: Annotated[VerificationService, Depends(get_verification_service)],
) -> VerifyResponse:
    return await cancel_on_disconnect(request, service.verify(body))


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
