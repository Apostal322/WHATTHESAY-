# Development guide

Commands, repository layout, manual testing and troubleshooting. Setup is in the
[README](../README.md#installation).

## Checks

```bash
# backend (from backend/)
pytest                    # 356 tests, no network, no key
ruff check . ../scripts && ruff format --check . ../scripts
mypy app                  # strict
python ../scripts/scan_secrets.py            # tracked files (CI); add --staged before a commit
python ../scripts/check_doc_links.py         # relative links and anchors in Markdown (CI)

# extension (from extension/)
npm test                  # vitest + Testing Library (46 tests)
npm run typecheck
npm run build
npm run e2e               # needs the build + a backend on :8000 (see below)
npm run screenshots       # README images; needs the build + `python -m tests.demo_server`
npm run check:remote-code # after the build: no remote or eval'd code in the package (MV3)
```

Repository layout:

```
backend/app/
  api/          routes (incl. NDJSON stream), dependency wiring
  core/         config, typed errors, JSON logging, TTL cache, rate limiter
  models/       API schemas, model-output schemas
  services/     youtube validation, Gemini adapter, prompts, extraction, retrieval
                (grounding / search + page fetch), verification + guardrails,
                attribution (identity, authenticity, entailment stage), temporal
                context, citation integrity
  evaluation/   eval dataset schema and metrics; reference records, offline evidence
                fixtures, chronology, proposition-level runs
backend/tests/  unit, API and adapter tests; e2e_server.py (smoke-test fixture) and
                demo_server.py (README screenshots), both scripted fake models
extension/src/
  content/      YouTube video context (read-only)
  background/   side panel behavior, message broker
  sidepanel/    App, hooks, Chrome bridge, styles
  components/   ClaimEditor, VerdictCard, SourceList, …
  shared/       API client, state machine, types, formatting
scripts/        poc_extract_claim.py, live_validation.py, verify_claim.py, capture_evidence.py,
                run_fixture_case.py, compare_retrieval.py, list_models.py, run_eval.py,
                scan_secrets.py, make_icons.py
eval/           dataset.example.jsonl, reference/ (answer keys), fixtures/ (offline evidence),
                factchecks/ (run artifacts); runs/ is git-ignored
docs/           API, development, architecture, evaluation, validation reports, security
```

## Testing the full user flow

0. **Run the live validation harness** (preflight, extraction against your
   human-verified moments, 20 s vs 50 s clipping, verification, cited-page screening):
   ```bash
   cp eval/live_cases.template.jsonl eval/live_cases.jsonl   # fill in real moments
   python scripts/live_validation.py eval/live_cases.jsonl --fetch-sources
   ```
   See [live-validation-report.md](live-validation-report.md).
1. **Or validate extraction on its own** (the riskiest part):
   ```bash
   python scripts/poc_extract_claim.py --url "https://www.youtube.com/watch?v=VIDEO_ID" --timestamp 755
   python scripts/poc_extract_claim.py --url "https://www.youtube.com/watch?v=VIDEO_ID" --timestamp 755 --probe-clipping
   ```
   The first command prints the extracted claim JSON with latency and token usage.
   The second checks whether Gemini honours clip offsets (see [feasibility](feasibility.md)).
2. Open a public `youtube.com/watch` video with speech (English or Russian) and open the side panel.
   It shows the video title and current position.
3. Just after a factual statement, click **Verify claim at mm:ss**. The video keeps playing.
4. Check the proposed claim and its quote/paraphrase label. Edit it if the wrong sentence
   was picked, then click **Check this claim** (or press Ctrl+Enter).
5. Watch the stages (analyzing, searching, evaluating), then read the verdict, the
   limitations and the sources. Open a source to confirm it says what the excerpt claims.
6. Edge cases to try:
   - a Shorts page (blocked with a message)
   - an ad playing
   - a private or region-locked video (offers manual entry)
   - an opinion such as "this is the best phone ever" (should return `not_verifiable`)
   - **Or type a claim manually** on any page
   - backend stopped or started without a key (clear status and errors)

Without a key you can still exercise the UI end to end with the scripted fixture backend:

```bash
cd backend && python -m tests.e2e_server      # fake model, canned data; clearly labelled
cd extension && npm run build && npm run e2e  # headless Chromium smoke test
```
`npm run e2e` launches Chromium from `CHROMIUM_PATH`. On Windows, point it at Chrome:
`$env:CHROMIUM_PATH="C:\Program Files\Google\Chrome\Application\chrome.exe"`.

## Diagnosing provider errors

Every provider failure is returned with a stable code. The backend logs (JSON) and the evaluation
artifacts also carry sanitized diagnostics: operation, model, HTTP status, provider status, quota id,
retry delay and the steps that completed before the failure. Diagnostics are never sent to the
extension.

| Code | Meaning | What to do |
|---|---|---|
| `provider_error` with HTTP 503 in the logs | The Gemini model is overloaded ("high demand") | Wait, or set `YOUVERIFY_FALLBACK_MODEL` (one fallback attempt per call, 503 only) |
| `provider_quota_exhausted` | The key's quota is used up: daily request cap, or a search-grounding quota | Do not retry in a loop. Wait for the reset (Google documents midnight Pacific for daily quotas) or use a key with more quota |
| `provider_rate_limited` | A short-window rate limit | Retry after a short wait |
| `retrieval_unavailable` | No evidence could be retrieved at all (search or page fetch outage). **Not a verdict** | Retry later; check search provider configuration and network |
| `backend_misconfigured` | e.g. paid search without a price or daily cap | Fix the `.env` settings named in the message |
| `missing_credentials` | `GEMINI_API_KEY` (or a search key) is not set | Configure it on the backend |

**Checking one claim without the extension (manual-claim mode):**

```bash
# typed claim (labelled "manual" in the artifact)
python scripts/verify_claim.py --claim "The Eiffel Tower was completed in 1889." --id eiffel
# or reuse a saved video extraction, with no new extraction call
python scripts/verify_claim.py --from-run eval/factchecks/<artifact>.json
```

This writes `eval/factchecks/<id>-<time>.json` plus a Markdown review sheet.

**A reference case, proposition by proposition, over offline evidence** (no search; 2 calls per
proposition; refuses to run until the fixture has captured excerpts):

```bash
python scripts/capture_evidence.py --check-only     # which hosts this network reaches (no API calls)
python scripts/capture_evidence.py                  # the app captures short verbatim excerpts
python scripts/run_fixture_case.py                  # dry run: plan and call count
python scripts/run_fixture_case.py --llm gemini --max-calls 10
```

The result is "live evaluation of offline evidence", never a live fact-check. A person fills in the
review section (quote and speaker checked, sources opened, verdict agrees). In the extension, use
**Type a claim manually**.

## Regenerating the README screenshots

The images in `docs/images/` are the real built extension in headless Chromium, talking to the
real backend with a **scripted fake model** (`backend/tests/demo_server.py`). All content is
synthetic and labelled `[DEMO]`; the safeguard notice is produced by the real attribution check.

```bash
cd backend && python -m tests.demo_server          # terminal 1
cd extension && npm run build && npm run screenshots   # terminal 2; writes docs/images/demo-*.png
```
