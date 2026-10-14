# API reference

The backend is a local FastAPI service (default `http://127.0.0.1:8000`).

Interactive docs: <http://127.0.0.1:8000/docs>. All errors look like
`{"error": {"code": "...", "message": "...", "retryable": bool}}`. The codes are
`invalid_input`, `video_unavailable`, `missing_credentials`, `model_not_found`,
`rate_limited`, `provider_rate_limited`, `provider_quota_exhausted`, `provider_timeout`, `provider_error`,
`retrieval_unavailable` (no evidence could be retrieved at all; not a verdict), `backend_misconfigured`,
`invalid_model_output` and `payload_too_large`.

```http
POST /v1/claims/extract
{"youtube_url": "https://www.youtube.com/watch?v=VIDEO_ID", "timestamp_sec": 755, "language": "auto"}
```
```json
{
  "status": "success",
  "video_id": "VIDEO_ID",
  "timestamp_sec": 755,
  "window": {"start_sec": 710, "end_sec": 765},
  "claim": "Artificial intelligence has already replaced 80% of software engineers.",
  "claim_type": "factual",
  "context": "…",
  "evidence_from_video": "AI has already replaced like 80 percent of software engineers",
  "is_exact_quote": false,
  "requires_user_confirmation": true,
  "language": "en",
  "reason": null,
  "model": "gemini-…",
  "latency_ms": 0,
  "cached": false,
  "usage": {"prompt_tokens": 0, "output_tokens": 0, "total_tokens": 0, "video_tokens": null, "audio_tokens": null, "estimated_cost_usd": null}
}
```
`status` is `no_claim_found` (with a `reason`) when nothing checkable was said.
The values above show the response shape, not a measured result.

```http
POST /v1/claims/verify          → VerifyResponse (JSON)
POST /v1/claims/verify/stream   → NDJSON: {"event":"stage","stage":"analyzing|searching|evaluating"} … {"event":"result","data":VerifyResponse}
{"claim": "…", "context": "…", "claim_type_hint": "factual", "video_id": "…", "timestamp_sec": 755, "language": "auto"}
```
`VerifyResponse` contains `claim`, `normalized_claim`, `claim_type`, `verdict`,
`explanation` (with `[S#]` citations), `key_findings`, `sources[]` (`id`, `title`, `url`,
`domain`, `retrieved_at`, `excerpts`, `stance`, `assessment_note`; `publisher` and
`published_date` are `null` unless known, never guessed), `limitations`,
`search_queries`, `independent_source_count`, `guardrail_notes` (advisory warnings),
`verdict_adjustments` (safeguards that changed the model's verdict: `rule`, `from_verdict`,
`to_verdict`, `reason`) and `usage`.
