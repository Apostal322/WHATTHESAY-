import pytest

from app.core.cache import TTLCache
from app.core.config import Settings
from app.core.errors import InvalidModelOutputError, VideoUnavailableError
from app.models.schemas import ExtractRequest, ExtractResponse
from app.services.extraction import ExtractionService, compute_window
from tests.conftest import EXTRACTION_OK, FakeLLM

VID = "dQw4w9WgXcQ"


def _req(**kw: object) -> ExtractRequest:
    base: dict[str, object] = {"video_id": VID, "timestamp_sec": 755}
    base.update(kw)
    return ExtractRequest.model_validate(base)


async def test_success_parsing_and_video_clip(settings: Settings) -> None:
    llm = FakeLLM(json_responses={"ExtractionOutput": EXTRACTION_OK})
    resp = await ExtractionService(llm, settings).extract(_req())

    assert resp.status == "success"
    assert resp.claim == EXTRACTION_OK["claim"]
    assert resp.claim_type == "factual"
    assert resp.is_exact_quote is False
    assert resp.requires_user_confirmation is True
    assert resp.usage.total_tokens == 120

    clip = llm.calls[0][1]["video"]
    assert clip.url == f"https://www.youtube.com/watch?v={VID}"
    assert (clip.start_sec, clip.end_sec) == (755 - 45, 755 + 10)
    assert "12:35" in llm.calls[0][1]["prompt"]


async def test_no_claim_found(settings: Settings) -> None:
    llm = FakeLLM(
        json_responses={"ExtractionOutput": {"found": False, "reason_if_not_found": "Only music."}}
    )
    resp = await ExtractionService(llm, settings).extract(_req())
    assert resp.status == "no_claim_found"
    assert resp.claim is None
    assert resp.reason == "Only music."


async def test_found_without_claim_text_is_not_success(settings: Settings) -> None:
    llm = FakeLLM(json_responses={"ExtractionOutput": {"found": True, "claim": "   "}})
    resp = await ExtractionService(llm, settings).extract(_req())
    assert resp.status == "no_claim_found"
    assert resp.reason


async def test_exact_quote_flag_requires_quote_text(settings: Settings) -> None:
    out = {**EXTRACTION_OK, "is_exact_quote": True, "evidence_from_video": None}
    llm = FakeLLM(json_responses={"ExtractionOutput": out})
    resp = await ExtractionService(llm, settings).extract(_req())
    assert resp.is_exact_quote is False


async def test_missing_claim_type_defaults_to_ambiguous(settings: Settings) -> None:
    out = {**EXTRACTION_OK, "claim_type": None}
    llm = FakeLLM(json_responses={"ExtractionOutput": out})
    resp = await ExtractionService(llm, settings).extract(_req())
    assert resp.claim_type == "ambiguous"


@pytest.mark.parametrize(
    "raw",
    [
        "not json at all",
        '{"found": "maybe"}',
        '{"claim": "x"}',
        '{"found": true, "claim_type": "fact"}',
    ],
)
async def test_malformed_model_output_raises(settings: Settings, raw: str) -> None:
    llm = FakeLLM(json_responses={"ExtractionOutput": raw})
    with pytest.raises(InvalidModelOutputError):
        await ExtractionService(llm, settings).extract(_req())


async def test_markdown_fenced_json_is_accepted(settings: Settings) -> None:
    import json

    raw = "```json\n" + json.dumps(EXTRACTION_OK) + "\n```"
    llm = FakeLLM(json_responses={"ExtractionOutput": raw})
    resp = await ExtractionService(llm, settings).extract(_req())
    assert resp.status == "success"


async def test_unavailable_video_propagates(settings: Settings) -> None:
    llm = FakeLLM(json_responses={"ExtractionOutput": VideoUnavailableError("private")})
    with pytest.raises(VideoUnavailableError):
        await ExtractionService(llm, settings).extract(_req())


async def test_cache_hit_avoids_second_model_call(settings: Settings) -> None:
    llm = FakeLLM(json_responses={"ExtractionOutput": EXTRACTION_OK})
    cache: TTLCache[ExtractResponse] = TTLCache(60, 10)
    service = ExtractionService(llm, settings, cache)
    first = await service.extract(_req(timestamp_sec=753))
    second = await service.extract(_req(timestamp_sec=755.9))  # same 3s bucket (251)
    assert len(llm.calls) == 1
    assert first.cached is False and second.cached is True


def test_window_is_clamped() -> None:
    w = compute_window(10, None, before=45, after=10)
    assert (w.start_sec, w.end_sec) == (0, 20)
    w = compute_window(100, duration_sec=104, before=45, after=10)
    assert (w.start_sec, w.end_sec) == (55, 104)


def test_request_validation() -> None:
    with pytest.raises(ValueError, match="youtube_url or video_id"):
        ExtractRequest.model_validate({"timestamp_sec": 3})
    with pytest.raises(ValueError, match="beyond the video duration"):
        ExtractRequest.model_validate({"video_id": VID, "timestamp_sec": 500, "duration_sec": 100})
    with pytest.raises(ValueError):
        ExtractRequest.model_validate({"video_id": VID, "timestamp_sec": -1})
    with pytest.raises(ValueError):
        ExtractRequest.model_validate({"video_id": VID, "timestamp_sec": 1, "language": "de"})
