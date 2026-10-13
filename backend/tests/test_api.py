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


def _search_settings(settings: Settings, **update: object) -> Settings:
    base = {"retrieval_backend": "search", "brave_api_key": SecretStr("k")}
    return settings.model_copy(update={**base, **update})


def test_paid_search_requires_price_and_daily_cap(settings: Settings) -> None:
    import httpx
    import pytest

    from app.api.deps import build_search_retriever
    from app.core.errors import ConfigurationError

    http = httpx.AsyncClient()
    with pytest.raises(ConfigurationError) as info:
        build_search_retriever(_search_settings(settings), http)
    msg = info.value.message
    assert "YOUVERIFY_PRICE_PER_1K_BRAVE" in msg and "YOUVERIFY_SEARCH_DAILY_BUDGET_USD" in msg

    ok = _search_settings(settings, price_per_1k_brave_requests=5.0, search_daily_budget_usd=2.0)
    retriever = build_search_retriever(ok, http)
    assert [p.name for p in retriever._search.providers] == ["brave"]
    assert retriever._search.budget is not None


def test_paid_fallback_needs_explicit_opt_in(settings: Settings) -> None:
    import httpx
    import pytest

    from app.api.deps import build_search_retriever
    from app.core.errors import ConfigurationError

    s = _search_settings(
        settings,
        search_providers="searxng,brave",
        searxng_url="http://searxng:8080",
        price_per_1k_brave_requests=5.0,
        search_daily_budget_usd=2.0,
    )
    http = httpx.AsyncClient()
    with pytest.raises(ConfigurationError) as info:
        build_search_retriever(s, http)
    assert "YOUVERIFY_ALLOW_PAID_SEARCH_FALLBACK" in info.value.message
    allowed = build_search_retriever(
        s.model_copy(update={"allow_paid_search_fallback": True}), http
    )
    assert [p.name for p in allowed._search.providers] == ["searxng", "brave"]


def test_searxng_alone_needs_no_budget(settings: Settings) -> None:
    import httpx

    from app.api.deps import build_search_retriever

    s = settings.model_copy(
        update={
            "retrieval_backend": "search",
            "search_providers": "searxng",
            "searxng_url": "http://searxng:8080",
        }
    )
    assert build_search_retriever(s, httpx.AsyncClient())._search.budget is None


def test_default_search_provider_is_brave(settings: Settings) -> None:
    assert Settings(_env_file=None).search_providers == "brave"  # type: ignore[call-arg]
