from app.core.logging import redact_secrets


def test_redacts_google_keys_tokens_and_query_params() -> None:
    text = (
        "key AIzaSyA1234567890abcdefghijklmnopqrstu, auth AQ.FAKEfakeFAKEfakeFAKEfake0000, "
        "Bearer ya29.a0AfH6SMBabcdefghijklmnopqrstuvwxyz, url https://x.test/v1?key=abc123&alt=json"
    )
    out = redact_secrets(text)
    for secret in ("AIzaSyA123", "AQ.FAKEfake", "ya29.a0AfH6", "abc123"):
        assert secret not in out
    assert "alt=json" in out


def test_leaves_ordinary_text_alone() -> None:
    text = "Quota exceeded for metric: generate_content_free_tier_requests, limit: 20"
    assert redact_secrets(text) == text
