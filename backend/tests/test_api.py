import json

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings
from app.core.errors import VideoUnavailableError
from app.main import create_app
from tests.conftest import ANALYSIS_FACTUAL, EXTRACTION_OK, FakeLLM, make_grounded

VID = "dQw4w9WgXcQ"


def test_health_reports_missing_credentials(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["gemini_configured"] is False


def test_extract_success(client: TestClient, fake_llm: FakeLLM) -> None:
    fake_llm.json_responses["ExtractionOutput"] = EXTRACTION_OK
    r = client.post(
        "/v1/claims/extract",
        json={"youtube_url": f"https://www.youtube.com/watch?v={VID}", "timestamp_sec": 755.4},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success"
    assert body["timestamp_sec"] == 755
    assert body["requires_user_confirmation"] is True
    assert "X-Request-ID" in r.headers


def test_validation_error_shape(client: TestClient) -> None:
    r = client.post(
        "/v1/claims/extract", json={"youtube_url": "https://evil.example/?v=1", "timestamp_sec": 1}
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_input"

    r = client.post("/v1/claims/extract", json={"video_id": VID, "timestamp_sec": "soon"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_input"

    r = client.post("/v1/claims/verify", json={"claim": "valid claim text", "unexpected": 1})
    assert r.status_code == 422


def test_unavailable_video_maps_to_422(client: TestClient, fake_llm: FakeLLM) -> None:
    fake_llm.json_responses["ExtractionOutput"] = VideoUnavailableError("Video is private.")
    r = client.post("/v1/claims/extract", json={"video_id": VID, "timestamp_sec": 5})
    assert r.status_code == 422
    assert r.json()["error"] == {
        "code": "video_unavailable",
        "message": "Video is private.",
        "retryable": False,
    }


def test_missing_credentials_without_fake(settings: Settings) -> None:
    with TestClient(create_app(settings)) as c:
        r = c.post("/v1/claims/verify", json={"claim": "The Eiffel Tower is in Berlin."})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "missing_credentials"


def test_verify_stream_emits_stages_then_result(client: TestClient, fake_llm: FakeLLM) -> None:
    fake_llm.json_responses["ClaimAnalysisOutput"] = ANALYSIS_FACTUAL
    fake_llm.grounded = make_grounded(chunks=[], supports=[])
    with client.stream(
        "POST", "/v1/claims/verify/stream", json={"claim": "AI replaced 80% of engineers."}
    ) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/x-ndjson")
        events = [json.loads(line) for line in r.iter_lines() if line]
    assert [e.get("stage") for e in events[:-1]] == ["analyzing", "searching"]
    assert events[-1]["event"] == "result"
    assert events[-1]["data"]["verdict"] == "insufficient_evidence"


def test_verify_stream_reports_errors_in_band(client: TestClient, fake_llm: FakeLLM) -> None:
    fake_llm.json_responses["ClaimAnalysisOutput"] = "garbage"
    with client.stream(
        "POST", "/v1/claims/verify/stream", json={"claim": "AI replaced 80% of engineers."}
    ) as r:
        events = [json.loads(line) for line in r.iter_lines() if line]
    assert events[-1] == {
        "event": "error",
        "error": {
            "code": "invalid_model_output",
            "message": "The model returned malformed JSON.",
            "retryable": True,
        },
    }


def test_rate_limit(settings: Settings, fake_llm: FakeLLM) -> None:
    fake_llm.json_responses["ExtractionOutput"] = EXTRACTION_OK
    limited = settings.model_copy(update={"rate_limit_per_minute": 2})
    with TestClient(create_app(limited, llm=fake_llm)) as c:
        codes = [
            c.post("/v1/claims/extract", json={"video_id": VID, "timestamp_sec": 5}).status_code
            for _ in range(3)
        ]
    assert codes == [200, 200, 429]


def test_body_size_limit(client: TestClient) -> None:
    r = client.post(
        "/v1/claims/verify",
        content=json.dumps({"claim": "x" * 20_000}),
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 413


def test_cors_allows_extension_origin_only(client: TestClient) -> None:
    ext = "chrome-extension://" + "a" * 32
    ok = client.options(
        "/v1/claims/verify",
        headers={"Origin": ext, "Access-Control-Request-Method": "POST"},
    )
    assert ok.headers.get("access-control-allow-origin") == ext
    bad = client.options(
        "/v1/claims/verify",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in bad.headers


def test_health_reports_retrieval_backend(client: TestClient) -> None:
    assert client.get("/health").json()["retrieval_backend"] == "gemini_grounding"


def test_search_backend_without_providers_fails_clearly(
    settings: Settings, fake_llm: FakeLLM
) -> None:
    fake_llm.json_responses["ClaimAnalysisOutput"] = ANALYSIS_FACTUAL
    s = settings.model_copy(update={"retrieval_backend": "search"})
    with TestClient(create_app(s, llm=fake_llm)) as c:
        r = c.post("/v1/claims/verify", json={"claim": "The Eiffel Tower is in Berlin."})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "missing_credentials"
    assert "YOUVERIFY_SEARXNG_URL" in r.json()["error"]["message"]
    assert not any(name == "grounded_search" for name, _ in fake_llm.calls)  # no silent fallback


def test_search_providers_follow_configured_order_and_skip_unconfigured(
    settings: Settings,
) -> None:
    import httpx

    from app.api.deps import build_search_providers

    s = settings.model_copy(
        update={
            "search_providers": "brave, searxng",
            "searxng_url": "http://searxng:8080",
            "brave_api_key": SecretStr("k"),
        }
    )
    http = httpx.AsyncClient()
    assert [p.name for p in build_search_providers(s, http)] == ["brave", "searxng"]
    only_searx = s.model_copy(update={"brave_api_key": None})
    assert [p.name for p in build_search_providers(only_searx, http)] == ["searxng"]
