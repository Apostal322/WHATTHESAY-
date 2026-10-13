# YouVerify AI

Fact-check a statement from the YouTube video you are watching without leaving the
video. Press **Verify claim** in Chrome's side panel. YouVerify finds the statement
near the current timestamp, lets you confirm or edit it, searches for evidence, and
returns an evidence-based verdict with clickable sources. When the evidence does not
settle the claim, it says so.

<p>
  <img src="docs/images/claim-confirmation.png" width="300" alt="Claim confirmation step">
  <img src="docs/images/verdict.png" width="300" alt="Verdict with sources">
</p>

<sub>Screenshots are from the automated E2E run, which uses canned fixture data, not a real verdict.</sub>

## Contents

- [How it works](#how-it-works)
- [Project status](#project-status)
- [Setup (Windows)](#setup-windows) · [macOS / Linux](#setup-macos--linux)
- [Testing the full user flow](#testing-the-full-user-flow)
- [API](#api)
- [Development](#development)
- [Known limitations](#known-limitations)
- [Future ML improvements](#future-ml-improvements)
- Docs: [live validation report](docs/live-validation-report.md) · [evidence retrieval architecture](docs/retrieval-architecture.md) · [feasibility report](docs/feasibility.md) · [evaluation protocol](docs/evaluation.md) · [security & privacy](docs/security-privacy.md)

## How it works

```
 Chrome                                              Backend (FastAPI, localhost)              Google
┌─────────────────────────────────────┐            ┌──────────────────────────────────────┐
│ content script (youtube.com/watch)  │            │ POST /v1/claims/extract               │
│  reads video id + currentTime       │            │  validate id → canonical URL          │   Gemini
│            ▲ GET_VIDEO_CONTEXT      │            │  clip [t-45s, t+10s], fps 0.5, low res ├──► video understanding
│ background service worker           │            │  → structured ExtractionOutput        │
│            ▲ GET_ACTIVE_VIDEO_CTX   │   HTTP     │                                      │
│ React side panel ───────────────────┼───────────►│ POST /v1/claims/verify/stream (NDJSON)│
│  idle → extracting → claim_ready    │            │  A  analyze: type + search queries    ├──► Gemini (JSON)
│  (user edits) → searching →         │◄───────────┤  C  grounded search                   ├──► Gemini + Google Search
│  evaluating → complete | error      │  stages +  │     sources ONLY from grounding meta  │
└─────────────────────────────────────┘  result    │  D  evaluate evidence (no tools)      ├──► Gemini (JSON)
                                                   │  F  guardrails: drop unknown ids,     │
                                                   │     downgrade unsupported verdicts    │
                                                   └──────────────────────────────────────┘
```

**Pipeline**

| Step | What happens | Where |
|---|---|---|
| A. Claim extraction | Gemini watches only a clip around the click. It returns the claim, its type (`factual` / `prediction` / `opinion` / `ambiguous` / `non_verifiable`), context, and what was said, flagged as exact quote or paraphrase. | `services/extraction.py` |
| B. Confirmation | The claim is always shown for editing before verification, because timestamp alignment is not guaranteed. | `ClaimEditor.tsx` |
| A′. Analysis | Re-classifies the (possibly edited) claim and plans 2–4 search queries. Opinions and predictions stop here with `not_verifiable`. | `services/verification.py` |
| C. Retrieval | Gemini with **Google Search grounding**. Citable sources are built **only** from `grounding_chunks`, with excerpts from `grounding_supports`. The model cannot add a URL. | `services/citations.py` |
| D–E. Evaluation | A separate tool-less call assesses each numbered source (`supports` / `contradicts` / `partially_supports` / `irrelevant`) and proposes a verdict using only those sources. | `prompts.py` |
| F. Integrity | Deterministic Python: drops assessments and citations with unknown IDs, downgrades a verdict to `insufficient_evidence` if no source was assessed as backing it, flags single-domain verdicts, merges duplicate URLs, counts independent domains. No sources means `insufficient_evidence` without any verdict call. | `verification.py`, `citations.py` |

Verdicts: `supported`, `contradicted`, `mixed`, `insufficient_evidence`, `not_verifiable`.
No numerical confidence is shown, because model self-reported confidence is not calibrated.

**Design choices**
- **Adapter boundary.** Business logic depends on an `LLMClient` protocol. `GeminiClient`
  is the only module that imports the SDK, and tests use a deterministic fake.
- **Model output is untrusted.** It is schema-constrained at generation time, re-validated
  with Pydantic, length-clipped and post-checked.
- **Streaming progress.** The UI shows the backend's actual stages, not a timer.
- **No database.** A small in-memory TTL cache with a `get`/`set` interface sits behind
  the services, so Redis can replace it without touching them.

## Project status

| Capability | Status |
|---|---|
| Backend API, pipeline, guardrails, error handling | ✅ Implemented; 199 unit/integration tests with a fake LLM |
| Gemini request wire format (YouTube clip, schema, search tool) and response parsing | ✅ Verified against a mocked HTTP transport using the real SDK (google-genai 2.29) and audited against current docs ([report](docs/live-validation-report.md)) |
| Chrome extension (MV3, side panel, content script, background) | ✅ Implemented; 39 component/unit tests; builds to `extension/dist` |
| End-to-end UI ↔ backend in a real Chromium | ✅ Automated smoke test (`npm run e2e`). Uses a **fixture YouTube page** and a **scripted fake model** |
| **Live Gemini extraction and clipping** | ⚠️ **Partly run** (2026-10-13, `gemini-3.8-flash`): extraction succeeded on 4/6 exploratory cases; clip offsets honoured in 3/3 paired 20 s/50 s runs. Claims are unlabelled, so no accuracy yet ([report](docs/live-validation-report.md)) |
| **Live verification** | ⛔ **0/6 completed**: the key's free-tier quota (20 requests/day/model) ran out. Re-run the failed cases with `scripts/live_validation.py --only …` |
| Evidence retrieval backends | ✅ Brave (recommended for public use), SearXNG and Gemini grounding, independently configurable. Paid search needs a price and a daily cap; paid fallback needs explicit opt-in. Outages return `retrieval_unavailable`, never `insufficient_evidence`. Pages are fetched DNS-pinned, quoted verbatim and grouped by independent origin. ⚠️ **No live comparison yet** (no Brave key; search and publisher sites blocked in the dev sandbox). See [architecture](docs/retrieval-architecture.md) §8 for the shortest path to one real end-to-end fact-check |
| **Live youtube.com page in Chrome** | ⚠️ **Not yet run** (YouTube blocked in the dev sandbox). Script: `npm run e2e:live` |
| Accuracy, latency and cost benchmarks | ⚠️ Extraction only: 21–88 s (median 31 s), about $0.005–0.013 per click at assumed prices. Accuracy and verification cost not measured. Public-release decision: **NO-GO** for now ([report](docs/live-validation-report.md) §7) |

## Setup (Windows)

**Requirements:** Windows 10/11, [Python 3.11+](https://www.python.org/downloads/),
[Node.js 22.12+](https://nodejs.org/) (LTS), Google Chrome 116+, and a
[Gemini API key](https://aistudio.google.com/apikey).

PowerShell, from the repository root:

```powershell
# 1. Configuration
Copy-Item .env.example .env
notepad .env                      # set GEMINI_API_KEY=...

# 2. Backend
cd backend
py -3.11 -m venv .venv            # or: python -m venv .venv
.\.venv\Scripts\Activate.ps1      # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -e ".[dev]"
python ..\scripts\list_models.py  # check which models your key can use; set YOUVERIFY_*_MODEL in .env if needed
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Check it at <http://127.0.0.1:8000/health>. `"gemini_configured": true` means the key was loaded.

In a **second** PowerShell window:

```powershell
# 3. Extension
cd extension
npm ci
npm run build                     # produces extension\dist
```

**4. Load in Chrome**
1. Open `chrome://extensions` and turn on **Developer mode** (top right).
2. Click **Load unpacked** and select the `extension\dist` folder.
3. Pin **YouVerify AI** from the puzzle-piece menu. Clicking its icon opens the side panel.
4. Recommended: copy the extension ID shown on the card and restrict the backend to it
   in `.env`, then restart the backend:
   `YOUVERIFY_ALLOWED_ORIGIN_REGEX=^chrome-extension://<your-id>$`

After rebuilding, click the reload icon on the extension card. Reload any YouTube tabs
that were open before the extension was installed.

## Setup (macOS / Linux)

```bash
cp .env.example .env && $EDITOR .env
cd backend && python3 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
uvicorn app.main:app --host 127.0.0.1 --port 8000
# new terminal
cd extension && npm ci && npm run build   # then load extension/dist as above
```

## Testing the full user flow

0. **Run the live validation harness** (preflight, extraction against your
   human-verified moments, 20 s vs 50 s clipping, verification, cited-page screening):
   ```bash
   cp eval/live_cases.template.jsonl eval/live_cases.jsonl   # fill in real moments
   python scripts/live_validation.py eval/live_cases.jsonl --fetch-sources
   ```
   See [docs/live-validation-report.md](docs/live-validation-report.md).
1. **Or validate extraction on its own** (the riskiest part):
   ```bash
   python scripts/poc_extract_claim.py --url "https://www.youtube.com/watch?v=VIDEO_ID" --timestamp 755
   python scripts/poc_extract_claim.py --url "https://www.youtube.com/watch?v=VIDEO_ID" --timestamp 755 --probe-clipping
   ```
   The first command prints the extracted claim JSON with latency and token usage.
   The second checks whether Gemini honours clip offsets (see [feasibility](docs/feasibility.md)).
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

## API

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
`search_queries`, `independent_source_count`, `guardrail_notes` and `usage`.

## Development

```bash
# backend (from backend/)
pytest                    # 199 tests, no network, no key
ruff check . ../scripts && ruff format --check . ../scripts
mypy app                  # strict

# extension (from extension/)
npm test                  # vitest + Testing Library (35 tests)
npm run typecheck
npm run build
npm run e2e               # needs the build + a backend on :8000 (see above)
```

Repository layout:

```
backend/app/
  api/          routes (incl. NDJSON stream), dependency wiring
  core/         config, typed errors, JSON logging, TTL cache, rate limiter
  models/       API schemas, model-output schemas
  services/     youtube validation, Gemini adapter, prompts, extraction,
                verification + guardrails, citation integrity
  evaluation/   eval dataset schema and metrics
backend/tests/  unit, API and adapter tests; e2e_server.py fixture
extension/src/
  content/      YouTube video context (read-only)
  background/   side panel behavior, message broker
  sidepanel/    App, hooks, Chrome bridge, styles
  components/   ClaimEditor, VerdictCard, SourceList, …
  shared/       API client, state machine, types, formatting
scripts/        poc_extract_claim.py, live_validation.py, list_models.py, run_eval.py, make_icons.py
eval/           dataset.example.jsonl (runs/ is git-ignored)
docs/           feasibility, evaluation, security & privacy
```

## Known limitations

- **Timestamp alignment is best-effort.** Gemini does not guarantee word-level
  transcription. The model may choose a neighbouring sentence, which is why
  confirmation is mandatory. "Exact quote" is a model self-report.
- **Clip offsets on YouTube URLs are unverified**, and if they are ignored cost
  scales with video length. Run `--probe-clipping`.
- Public `youtube.com/watch` videos only; no Shorts, live streams, private or unlisted videos.
  The Gemini free tier allows 8 h of YouTube video per day.
- **Excerpts are grounded summaries**, not verbatim page text: Google Search grounding
  attributes the research model's sentences to sources. The evaluator judges those
  summaries, not full pages, so open the sources.
- Source `publisher` and `published_date` are not available from grounding metadata,
  so they are left empty rather than guessed.
- The evaluator's per-source stances are themselves model judgements. Guardrails
  ensure consistency, not correctness.
- Single-user local backend: no auth, and in-memory cache and rate limiting.
- The default model ID (`gemini-3.8-flash`) could not be checked against the live API.
  Verify it with `scripts/list_models.py`.

## Future ML improvements

- **Measured evaluation:** build a labelled set of 100+ EN/RU video moments, track
  false-confident rate as the primary metric, and run A/B tests on prompts, windows and models.
- **Better claim localisation:** use official caption tracks where the creator provides
  them for exact quotes and alignment, plus a claim-detection model to rank candidate
  sentences in the window.
- **Read the sources:** fetch cited pages (with SSRF-safe allowlisting or Gemini's URL
  context tool) and extract verbatim supporting passages. Then add an NLI model to
  cross-check stances independently of the LLM.
- **Retrieval quality:** query rewriting per claim type, re-ranking, topic-aware
  source-quality priors, and detection of syndicated copies beyond URL dedupe.
- **Calibration:** once enough labelled data exists, learn when to abstain, and only then
  consider showing calibrated confidence.
- **Infrastructure:** Redis cache keyed by (video, window) and claim hash; persistent
  run logs for offline evaluation.
