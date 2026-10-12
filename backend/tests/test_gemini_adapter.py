"""Tests the real GeminiClient against a mocked HTTP transport (no network, no key).

These pin the wire format we rely on (YouTube file_uri + clip offsets, schema,
search tool) and the provider-error classification.
"""

import json
from typing import Any

import httpx
import pytest

from app.core.errors import (
    MissingCredentialsError,
    ModelNotFoundError,
    ProviderError,
    ProviderRateLimitedError,
    VideoUnavailableError,
)
from app.models.llm_outputs import ExtractionOutput
from app.services.llm import GeminiClient, VideoClip

CLIP = VideoClip(
    url="https://www.youtube.com/watch?v=dQw4w9WgXcQ", start_sec=710, end_sec=765, fps=0.5
)


def _ok_body() -> dict[str, Any]:
    return {
        "candidates": [
            {
                "content": {"role": "model", "parts": [{"text": '{"found": false}'}]},
                "finishReason": "STOP",
                "groundingMetadata": {
                    "webSearchQueries": ["q1"],
                    "groundingChunks": [
                        {
                            "web": {
                                "uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/a",
                                "title": "bls.gov",
                                "domain": "bls.gov",
                            }
                        },
                        {"retrievedContext": {"uri": "gs://x"}},
                    ],
                    "groundingSupports": [
                        {"segment": {"text": "Employment grew."}, "groundingChunkIndices": [0, 1]},
                        {"segment": {"text": ""}, "groundingChunkIndices": [0]},
                    ],
                },
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 5000,
            "candidatesTokenCount": 40,
            "thoughtsTokenCount": 10,
            "totalTokenCount": 5050,
            "promptTokensDetails": [
                {"modality": "VIDEO", "tokenCount": 3000},
                {"modality": "AUDIO", "tokenCount": 1500},
            ],
        },
    }


def _client(handler: Any) -> GeminiClient:
    return GeminiClient(
        "test-key", timeout_sec=5, max_retries=0, transport=httpx.MockTransport(handler)
    )


async def test_video_request_wire_format_and_usage() -> None:
    sent: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append(json.loads(req.content))
        return httpx.Response(200, json=_ok_body())

    res = await _client(handler).generate_json(
        model="m", system="sys", prompt="find claim", schema=ExtractionOutput, video=CLIP
    )
    part = sent[0]["contents"][0]["parts"][0]
    assert part["fileData"]["file_uri"] == CLIP.url
    assert part["videoMetadata"] == {"start_offset": "710s", "end_offset": "765s", "fps": 0.5}
    gen = sent[0]["generationConfig"]
    assert gen["responseMimeType"] == "application/json"
    assert gen["mediaResolution"] == "MEDIA_RESOLUTION_LOW"
    assert "factual" in gen["responseSchema"]["properties"]["claim_type"]["enum"]

    assert res.text == '{"found": false}'
    assert (res.usage.prompt_tokens, res.usage.output_tokens) == (5000, 50)
    assert (res.usage.video_tokens, res.usage.audio_tokens) == (3000, 1500)


async def test_grounded_search_parses_metadata() -> None:
    sent: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append(json.loads(req.content))
        return httpx.Response(200, json=_ok_body())

    res = await _client(handler).grounded_search(model="m", system="s", prompt="p")
    assert sent[0]["tools"] == [{"googleSearch": {}}]
    assert [s.domain for s in res.sources] == ["bls.gov"]  # non-web chunk skipped
    assert [(s.text, s.source_indices) for s in res.supports] == [("Employment grew.", (0,))]
    assert res.queries == ["q1"]


@pytest.mark.parametrize(
    ("status", "message", "video", "expected"),
    [
        (429, "Resource exhausted", False, ProviderRateLimitedError),
        (404, "models/x is not found for API version v1beta", False, ModelNotFoundError),
        (400, "API key not valid. Please pass a valid API key.", False, MissingCredentialsError),
        (403, "The YouTube video is private or unavailable", True, VideoUnavailableError),
        (400, "Request contains an invalid argument: file_uri", True, VideoUnavailableError),
        (400, "Invalid schema", False, ProviderError),
        (503, "overloaded", False, ProviderError),
    ],
)
async def test_error_classification(
    status: int, message: str, video: bool, expected: type[Exception]
) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status, json={"error": {"code": status, "message": message, "status": "X"}}
        )

    with pytest.raises(expected):
        await _client(handler).generate_json(
            model="m",
            system="s",
            prompt="p",
            schema=ExtractionOutput,
            video=CLIP if video else None,
        )


async def test_missing_key_raises_on_use_not_construction() -> None:
    client = GeminiClient(None, timeout_sec=5, max_retries=0)
    with pytest.raises(MissingCredentialsError):
        await client.grounded_search(model="m", system="s", prompt="p")


async def test_timeout_is_classified() -> None:
    from app.core.errors import ProviderTimeoutError

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=req)

    with pytest.raises(ProviderTimeoutError):
        await _client(handler).grounded_search(model="m", system="s", prompt="p")
