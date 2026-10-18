# DevOps handoff

What the application needs from infrastructure before anyone but its developer uses it. The
application code is ready for these changes (interfaces exist); none of it is built.

## 1. Configuration

All settings are environment variables, documented in [`.env.example`](../../.env.example).
Required for a shared deployment, beyond the local defaults:

| Variable | Why |
|---|---|
| `GEMINI_API_KEY` | Secret store only; never in images, logs or the extension |
| `YOUVERIFY_GEMINI_DAILY_CALL_LIMIT` | Hard daily cap on model calls **per process**; divide the real budget by the number of workers until it is shared (§3) |
| `YOUVERIFY_ALLOWED_ORIGIN_REGEX` | Exactly the published extension's origin: `^chrome-extension://<id>$` |
| `YOUVERIFY_RATE_LIMIT_PER_MINUTE` | Per client address; see §3 for proxies |
| `YOUVERIFY_SEARCH_DAILY_BUDGET_USD`, `YOUVERIFY_PRICE_PER_1K_BRAVE` | Required by the app before any paid search provider is used |
| `YOUVERIFY_LOG_LEVEL` | `INFO` in production; logs carry no claim text and no secrets |

## 2. Network and transport

- **HTTPS** in front of the API. The extension's `host_permissions` and `VITE_BACKEND_URL` must
  name that HTTPS origin; plain HTTP is only for `127.0.0.1`.
- **Egress policy:** block private, link-local and metadata ranges for the backend's outbound
  traffic (defence in depth for `fetch_page`, which already pins DNS). If outbound traffic uses
  an HTTP proxy, the proxy resolves names itself and must apply the same policy.
- Streaming responses (`/v1/claims/verify/stream`, NDJSON) must not be buffered by the proxy
  (`X-Accel-Buffering: no` or equivalent), and idle timeouts must exceed 300 s.
- **Client disconnects must reach the app:** the API cancels model calls when the client goes
  away (H-9). A proxy that keeps the upstream connection open after the client left defeats this.

## 3. State that is per process today

| Component | Today | Needed with more than one worker |
|---|---|---|
| Rate limiter | In-memory sliding window keyed by `request.client.host` | Shared store (e.g. Redis); behind a proxy, key by the real client from a **trusted** `X-Forwarded-For` |
| Daily model-call cap, search spend guard | In-memory `SpendGuard` | Shared atomic counter with the same reserve/refund semantics |
| Result caches (`TTLCache`) | In-memory, 1 h TTL | Optional shared cache; keys already include context, model, retriever and policy version |
| Request coalescing (`SingleFlight`) | Per process | Acceptable per process; a shared lock is optional |

Interfaces are small (`get`/`set`, `try_reserve`/`refund`, `check`), so each can move to Redis
without touching the services.

## 4. Abuse and cost

- The API is **anonymous** today. A public deployment needs per-installation or per-user
  identity, per-user quotas, and a global daily spend ceiling that is enforced across workers.
- Cost amplification paths that are already bounded in code: request bodies (16 KB, streamed
  check), fields, pages fetched per claim, redirects, decompression, SDK retries
  (`YOUVERIFY_MAX_RETRIES`), and duplicate concurrent requests (coalesced).

## 5. Observability

- Logs are JSON lines on stdout. Every line written while serving a request carries
  `request_id` (also returned as the `X-Request-ID` header).
- `claim_verified` carries `stage_ms` (analysis, retrieval, evaluation), `total_tokens`,
  `estimated_cost_usd` (when prices are configured), `safeguards` (verdict rules that fired),
  `retrieval` and `verdict`. Provider failures log `provider_error` with sanitized diagnostics
  (HTTP status, quota id, retry delay), and the cache and coalescing events are logged.
- Needed: log shipping, dashboards for p50/p95 latency per stage, error rate per code
  (`provider_quota_exhausted`, `retrieval_unavailable`, ...), daily spend, and an alert when the
  daily cap is near.

## 6. Release and rollback

- **Order:** deploy the backend first. API changes are additive (new optional response
  fields; the extension tolerates their absence), so an older extension keeps working with a
  newer backend.
- **Rollback:** redeploy the previous backend image; in-memory caches are discarded on restart.
  Bump `VERIFICATION_POLICY_VERSION` (in `verification.py`) whenever prompts or safeguards
  change, so a shared cache never serves verdicts from an older policy.
- **Acceptance per release:** CI green (lint, types, tests, build, secret scan, link check,
  dependency audit, remote-code check, browser smoke), then on staging: `/health` reports the
  intended models and retrieval backend, and one scripted verification passes.

## 7. Acceptance criteria for a closed beta

1. HTTPS endpoint; CORS restricted to the published extension id.
2. Shared rate limiting and a shared daily spend cap, verified by a load test that the cap holds
   with N workers.
3. Disconnect cancellation verified through the production proxy (repeat the H-9 test against
   staging).
4. Dashboards and alerts from §5.
5. Secrets in a secret manager; the exposed development key rotated.
