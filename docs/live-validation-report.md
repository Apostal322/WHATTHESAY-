# Live integration validation: report (2026-10-12)

**Bottom line: the core workflow has *not* been validated against the live
Gemini API or a real youtube.com page.** Both were blocked in the development
environment (details below). No live results are reported here, because none
exist. This report records what was checked, what changed, the exact blockers,
and the commands that produce the missing measurements in one run.

## 1. Blockers (exact)

| Needed for | Blocker | Evidence |
|---|---|---|
| Steps 3–7: model listing, extraction, clipping, verification | **No `GEMINI_API_KEY`** in the environment or `.env` | `https://generativelanguage.googleapis.com/v1beta/models` is reachable and answers Google's own `403 PERMISSION_DENIED: Method doesn't allow unregistered callers … use API Key`. The API is reachable; only the key is missing. Gemini fetches YouTube videos server-side, so **a key alone unblocks steps 3–7.** |
| Step 8: extension on real youtube.com | Network policy denies `www.youtube.com` | Egress proxy: `connect_rejected` for `www.youtube.com:443`; Chromium: `net::ERR_TUNNEL_CONNECTION_FAILED` from `extension/e2e/live.mjs` |
| Step 1: reading the official docs directly | Network policy denies `ai.google.dev` and `aistudio.google.com` | Proxy `connect_rejected`; the audit used search-engine extracts of those pages plus the installed SDK source |

## 2. Results table

| # | Check | Result | Basis |
|---|---|---|---|
| 1 | Integration matches current docs | ✅ **No defect found** (see §3) | Docs via search extracts; SDK 2.29.0 source (still the latest release) |
| 2 | SDK request compatible with live API | ✅ Wire format matches the documented shape · ⚠️ not confirmed by a live call | Mocked-transport tests pin `fileData.file_uri`, `videoMetadata{start_offset,end_offset,fps}`, `responseSchema`, `mediaResolution`, `tools[googleSearch]` |
| 3 | `list_models.py` / model availability | ⛔ Not run (no key) | `live_validation.py` preflight aborts if the configured model is missing |
| 4 | Extraction on ≥3 public videos (EN+RU) | ⛔ Not run (no key) | — |
| 5 | Extraction vs. ground truth | ⛔ Not run; no human-verified cases exist yet | Template: `eval/live_cases.template.jsonl` |
| 6 | Clipping 20 s vs 50 s | ⛔ Not run (no key) · **open risk** (see §3) | Harness compares media tokens, latency, F1 and cost per window |
| 7 | Live verification and source support | ⛔ Not run (no key) | Harness fetches each cited page, screens excerpts, and emits a manual review sheet |
| 8 | Extension on real youtube.com | ⛔ Blocked by network policy | `npm run e2e:live` written and syntax-checked; failed here with the tunnel error above |
| 9 | Fix confirmed defects and rerun tests | ✅ 2 cost-accounting defects fixed (§4); all suites green | 108 backend tests, 35 frontend tests, typecheck, build, mocked browser E2E |

## 3. Docs audit findings

| Topic | Current documentation / evidence | Our implementation | Verdict |
|---|---|---|---|
| API surface | `generateContent` still supported. Interactions API is the newer path but **does not support `video_metadata` (clipping/fps)** | `client.aio.models.generate_content` | Correct; no rewrite |
| YouTube input | Public URL as `file_data.file_uri`; public videos only; free tier 8 h/day | Canonical watch URL from a validated ID | Correct |
| Static vs agentic | Static (default) samples frames at fixed fps. Agentic (Interactions, opt-in, 3.8/3.6 Flash, 3.5 Flash-Lite) navigates the timeline; offsets/fps don't apply | Static with clip offsets | Correct for short windows. Agentic is a fallback if clipping fails |
| Clipping | Documented via `videoMetadata.start_offset/end_offset` · **public report of offsets ignored for YouTube on 3.7 Flash** ([video-intel#141](https://github.com/dzivkovi/video-intel/issues/141)) | Same fields | **Unresolved risk; must be measured** |
| Media resolution | Gemini 3 video frames: 70 tokens (`low`/`medium`), 280 (`high`) | `low`, fps 0.5 | Correct; docs corrected (previously said ~66) |
| Model | `gemini-3.8-flash` is listed as GA. Third-party price: $0.75/$3.75 per 1M tokens (intro through 2026-12-31) | Default model | Plausible; **unconfirmed against the live model list** |
| Search grounding | `uri` is a `vertexaisearch` redirect and `title` is often a bare domain. Gemini 3 bills **per executed search query** (5k/month free, then $14/1k on Google Cloud's pricing page) | Redirect resolved without following; domain falls back to title | Parsing correct · **cost accounting was wrong** (fixed, §4) |
| Search + JSON schema in one call | Not confirmed by any source | Not used: grounded search returns free text, and evaluation is a separate tool-less JSON call | Correct; avoids the unknown |

## 4. Defects fixed in this round

| Defect | Impact | Fix | Test |
|---|---|---|---|
| Cost estimate ignored grounding search fees, which are billed per query on Gemini 3 | Under-reported cost per verification, potentially by a large factor, because each verification can run several billable searches | `usage.search_queries` + `YOUVERIFY_PRICE_PER_1K_SEARCHES` included in `estimated_cost_usd` | `test_cost_includes_executed_search_queries` |
| `search_queries` fell back to *planned* queries when grounding reported none | Would overcount billable searches and misreport what was searched | Report only queries Google says it executed | `test_planned_queries_are_not_reported_as_executed` |

No live-integration defect could be *confirmed*, because nothing ran live.
Possible defects that only a live run can show are listed in §6.

## 5. Measured performance and costs

**None measured.** The cost model in [feasibility.md](feasibility.md) is an
estimate (≈ 4–5k input tokens per extraction if clipping works; 3 model
calls plus N billed searches per verification). `live_validation.py` reports
actual tokens, latency and cost per call.

## 6. Remaining risks (highest first)

1. **Clipping ignored for YouTube URLs.** Every click would bill the full video, and
   the model could pick a statement from anywhere in it, with shifted
   timestamps. The 20 s vs 50 s experiment detects this.
2. **Wrong sentence selected** even when clipping works. This is mitigated by mandatory
   confirmation, but accuracy is unknown.
3. **Model ID or availability:** `gemini-3.8-flash` is unconfirmed for this key.
4. **Excerpts don't support conclusions.** Grounded excerpts are model summaries;
   the evaluator judges summaries, not pages. The harness screens each page and
   produces a manual review sheet, but a human must still judge support.
5. **Unrecognised error wording** for private, region-locked or age-restricted videos. This could
   surface as a generic `provider_error` instead of `video_unavailable`, which degrades the UX
   but not safety.
6. **Real YouTube DOM in the content script:** the selectors (`#movie_player video`,
   `.ad-showing`, `h1.ytd-watch-metadata`, the `yt-navigate-finish` event) are long-standing,
   but none was exercised on the live site.
7. **Latency:** Gemini 3 thinking defaults may make extraction slow. Unmeasured.

## 7. Commands to complete validation

On a machine with a key and YouTube access (or in this cloud environment, after
adding the `GEMINI_API_KEY` environment secret and allowing `www.youtube.com`
in Network access, then starting a new session):

```bash
# 0. setup (once)
cp .env.example .env            # set GEMINI_API_KEY
cd backend && python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]" && cd ..

# 1. model availability (step 3)
python scripts/list_models.py

# 2. build >=3 human-verified cases (EN + RU): watch each video, note the exact
#    words just before a timestamp, set verified_by. Rows containing REPLACE are skipped.
cp eval/live_cases.template.jsonl eval/live_cases.jsonl && $EDITOR eval/live_cases.jsonl

# 3. steps 3-7 in one run (preflight, extraction, 20 s/50 s clipping, verification + page screen)
python scripts/live_validation.py eval/live_cases.jsonl --fetch-sources \
    --price-in 0.75 --price-out 3.75 --price-search-1k 14      # confirm current prices first
#    -> eval/runs/live-<ts>/report.md : fill in the "supports conclusion?" column by hand

# 4. step 8: real youtube.com in Chromium/Chrome with the built extension
uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 &
cd extension && npm ci && npm run build
CHROMIUM_PATH="/path/to/chrome" node e2e/live.mjs --url "https://www.youtube.com/watch?v=ID" --at 755 --headed
# and the manual checklist in README "Testing the full user flow" in normal Chrome
```

## 8. Prioritized next steps

1. **Run §7 with a key** and record results in this report (replacing §2 rows 3–8).
2. **Decide on clipping:**
   - If it is honoured, keep the current design.
   - If it is ignored, either move extraction to agentic processing on the Interactions API, or
     guard each request with a media-token check that fails closed. The guard would flag
     `clip_not_honoured` instead of presenting a possibly wrong sentence.
3. If error wording differs, update `_classify_api_error` from the observed
   private/region-locked/age-restricted responses.
4. Measure latency. If it is high, test a lower thinking level for extraction and
   compare F1 and latency.
5. Grow the labelled set to 30–50 moments and run `scripts/run_eval.py`. Track the
   false-confident rate as the primary metric.
6. Only after 1–5: consider product features.
