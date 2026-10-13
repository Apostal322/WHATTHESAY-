# Live integration validation: report (updated 2026-10-13)

**Bottom line.** The first live runs against the real Gemini API took place on
2026-10-13 with `gemini-3.8-flash` on the two evaluation videos.

- **Extraction worked:** 4 of 6 cases (plus 1 smoke run).
- **Clipping was honoured:** in all 3 paired 20 s / 50 s experiments.
- **Verification did not complete:** 0 of 6. The key is on the Gemini **free tier**,
  capped at **20 requests per day per model**, and the run hit that cap before any
  verification finished.
- **Not measured:** source quality, verdict support and political-context handling
  remain **unmeasured**.

**No accuracy or F1 is reported.** All six cases are *exploratory*: no human has
labelled what is said at these timestamps. Every claim below is
**model output, not ground truth**.

**Decision: NO-GO** for a public Chrome extension in the current architecture
(§7). The core pipeline design (clip, extract, confirm, ground, guardrails) is
holding up so far, but serving, quota and quality gates are not met.

## 1. What ran

| Time (UTC) | Run | Successful model calls | Outcome |
|---|---|---|---|
| 01:50 | smoke: `ru-nirvana-193`, extraction only | 1 | ✅ |
| 01:50–02:00 | all 6 cases: extraction, 20 s/50 s clipping, verification (`--verify-from extracted --fetch-sources`) | 10 (extraction and clipping) | ⚠️ 429 from the first verification onward; extraction/clipping 429 from case 4 |
| 02:02 | diagnostic probe: 1 JSON call and 1 grounded-search call, no retries | 0 | 429 `GenerateRequestsPerDayPerProjectPerModel-FreeTier`, `retryDelay` 79080 s |

Settings: window −45 s/+10 s, fps 0.5, `media_resolution=low`, no thinking
config (model default). Cost estimates use **unconfirmed** prices: $0.75 per 1M
input tokens, $3.75 per 1M output tokens, $14 per 1k searches. Output includes
thinking tokens.

## 2. Results, all six cases (automatically generated; no ground truth)

| case | lang | t (s) | extracted claim (product path, 55 s window) | type | exact quote? (model-reported) | latency s | in / out tokens | cost $ | 20 s vs 50 s clipping | verification |
|---|---|---|---|---|---|---|---|---|---|---|
| ru-nirvana-193 | ru | 193 | «По словам писателя Виктора Пелевина, любая религия, кроме буддизма, создана для анестезии и мобилизации на работу.» | factual | no | 29.0 | 3642 / 1510 | 0.0084 | honoured: 1161 → 2901 video tok (×2.50 for a ×2.50 window) | **failed:** quota 429 |
| ↳ same case, smoke run | ru | 193 | «Все религии, кроме буддизма, — это анестезия для выключения чувств.» | **opinion** | yes | 16.6 | 3642 / 943 | 0.0063 | – | – |
| ru-nirvana-225 | ru | 225 | «Иисус Христос нёс свой крест.» (heard: «Вот Христос свой крё… крест нёс») | factual | no | **88.5** | 3642 / 2487 | 0.0121 | honoured: 1161 → 2901 | **failed:** quota 429 |
| en-debate-0600 | en | 600 | "Immigrants entering the United States are taking jobs that are currently occupied by African Americans and Hispanics." | factual | yes | 32.8 | 3576 / 735 | 0.0054 | honoured: 1161 → 2901 · **20 s window picked a different statement** ("…pouring in from prisons, jails, mental institutions…") | **failed:** quota 429 |
| en-debate-1200 | en | 1200 | "Donald Trump reversed course and said he would vote to support the six-week abortion ban." (heard: "you reversed course…") | factual | yes | 20.7 | 3576 / 2623 | 0.0125 | **failed:** quota 429 | **failed:** quota 429 |
| en-debate-1800 | en | 1800 | **failed:** quota 429 | – | – | – | – | – | **failed:** quota 429 | not run (no claim) |
| en-debate-2400 | en | 2400 | **failed:** quota 429 | – | – | – | – | – | **failed:** quota 429 | not run (no claim) |

Failed stages appear only as failures. None of them contributes to the latency, token or cost figures below.

## 3. Analysis

### 3.1 Does the clip window change how much video is processed?

**Yes, for `gemini-3.8-flash` on these two public videos.** This conclusion does not rest on one extraction:

- **Paired runs:** in all 3 paired 20 s / 50 s runs (2 videos, 3 timestamps), video tokens scaled exactly
  with the window: 1161 → 2901, ratio 2.50 for a 2.50× longer window.
- **Constant rate:** the rate held at about **58 tokens per second of clip** across 20, 50 and 55 s windows
  in 7 of 7 successful video requests.
- **No full-video billing:** if offsets were ignored, the debate request (sampled up to t = 1210 s) would
  have billed at least about 70k media tokens. It billed about 3.6k.
- **Start offset applied:** different timestamps returned different, locally coherent statements, so the
  start offset is honoured and not just the length.

Not established:

- Behaviour on other models. The public report about 3.7 Flash is still open.
- Behaviour on other videos (long, live, region-locked).
- Whether the chosen sentence is the one *immediately* before *t*. That needs a human.

Separately, `audio_tokens` is never reported for YouTube input. Audio appears to be counted inside the
VIDEO modality, so the harness's "expected" figure (which assumes 32 audio tokens per second) is about
13% high (1161 observed vs 1340 expected). The ratio test is unaffected.

### 3.2 Is the correct spoken statement extracted at the timestamp?

**Unknown: there are no labels.** What the runs do show:

- **Plausibility:** the extracted text is a plausible, near-verbatim rendering of speech in every
  successful case. The evidence strings keep disfluencies such as «крё… крест», a sign of real
  transcription rather than invention.
- **Window-dependent selection:** the statement chosen depends on the window. For en-debate-0600, the 20 s
  and 50 s windows returned *different* statements. For ru-nirvana-193 they returned two related but
  different sentences.
- **Non-deterministic:** the same request (ru-nirvana-193, same window) gave a different claim, claim
  type (opinion vs factual) and exact-quote flag in two runs 10 minutes apart.

The mandatory confirmation step is therefore essential, not optional.

### 3.3 Russian vs English

There are too few cases to compare: 2 RU and 2 EN successful extractions, no labels. Observations only:

| | RU | EN |
|---|---|---|
| Latency | 29 s and 88 s | 33 s and 21 s |
| Model-flagged exact quote | 0/2 | 2/2 |

Russian claims were kept in Russian, as designed.

### 3.4–3.5 Source trustworthiness, relevance, independence, and verdict support

**Not measured: 0 verifications completed.** The source-review sheet is empty, and nothing can be said
about source quality or verdict support.

### 3.6 Philosophical and religious claims vs empirical facts

This is a **real weakness, already visible in the extraction step**:

- **Unstable classification:** ru-nirvana-193 ("every religion except Buddhism exists for anaesthesia")
  was classified *opinion* in one run. In the other it was reframed as an attribution ("according to
  Pelevin…") and classified *factual*. Only the attribution (did Pelevin write this?) is checkable; the
  religious thesis itself is not.
- **Doctrinal narrative treated as fact:** ru-nirvana-225 turned a passing aside into "Jesus Christ carried
  his cross", classified *factual*. That is a doctrinal and historical narrative, and its sources disagree:
  John vs. the Synoptics' Simon of Cyrene.

The analysis step (A′) re-classifies before searching, so these might still end as `not_verifiable`.
That is untested.

### 3.7 Political claims in their original context

**Verification was not run, so this is not measured.** On the extraction side:

- **Pronoun resolved:** en-debate-1200 resolved "you" to "Donald Trump". Correct pronoun resolution, but
  the speaker identity is a model inference.
- **Speaker and date dropped:** en-debate-0600 was rewritten as a general, timeless assertion about
  immigrants, losing *who* said it and *when* (the 2024 debate).
- **Harness defect fixed:** the harness neither recorded the extraction `context` nor passed it to
  verification. The extension *does* pass it (`App.tsx`), so the harness was not testing the product path
  (see §5).

### 3.8 Latency, tokens, search usage and cost

Only from successful calls:

| Measurement | n | Range | Median |
|---|---|---|---|
| Extraction latency (product path, 55 s) | 4 | 20.7–88.5 s | 30.9 s |
| Extraction latency, 20 s window | 3 | 8.4–41.4 s | – |
| Extraction latency, 50 s window | 3 | 9.1–59.5 s | – |
| Input tokens per extraction | 4 | 3576–3642 | – |
| Output tokens incl. thinking | 4 | 735–2623 | – |
| Cost per extraction | 4 | $0.0054–0.0125 | – |

- **Input is stable** (clipping works).
- **Output, mostly thinking tokens, drives cost and latency:** 51–79% of the cost, and the 88 s outlier.
- **Verification** latency, tokens, searches and cost: **not measured** (0 completed).

## 4. Diagnosis of `provider_error`

| | Finding |
|---|---|
| Error class | `ProviderRateLimitedError` (`provider_rate_limited`), the same for all 9 failed stages. 2 further verifications were not run because extraction had failed. |
| HTTP / provider status | 429 `RESOURCE_EXHAUSTED` |
| Quota | `generativelanguage.googleapis.com/generate_content_free_tier_requests`, quota id `GenerateRequestsPerDayPerProjectPerModel-FreeTier`, limit **20**, model `gemini-3.8-flash` |
| Retry | `retryDelay` 79080 s (about 22 h) |
| Failed operations | First: verification. Each case runs extraction → clipping → verification, so the third verification attempt was past the 20th request. Later: extraction and clipping as well. The probe showed structured JSON and grounded search are both blocked. |
| Transient? | No. Reproduced with a single no-retry probe after the run. Quota-related, not specific to either video. |
| Why it was opaque | HTTP status and provider message were attached only as logging extras, and the script did not configure logging, so the console printed only `provider_error`. `results.json` kept only the generic user message. |

**Failed cases were not retried.** The provider asks for a 22-hour wait, so a retry today on this model can only fail.
To finish, run only the cases that failed, after the reset or with a billed key:

```bash
python scripts/live_validation.py eval/live_cases.jsonl --verify-from extracted --fetch-sources \
    --price-in 0.75 --price-out 3.75 --price-search-1k 14 \
    --only ru-nirvana-193,ru-nirvana-225,en-debate-0600,en-debate-1200,en-debate-1800,en-debate-2400
```

All six still need verification. That is roughly 6 × (1 extraction + 2 clipping + at least 3 verification
calls), so at least 36 requests: **more than one free-tier day**. Run it on a billed key, or add
`--skip-clipping` (clipping already has evidence for 3 cases) and spread it over two days.

## 5. Changes in this round (all covered by tests; no fallbacks added)

| Change | Why | Test |
|---|---|---|
| Errors carry sanitized `diagnostics`: operation (`video_extraction` / `structured_json` / `grounded_search`), model, HTTP status, provider status, redacted provider message, `QuotaFailure` ids and `RetryInfo` delay. Logged as structured JSON, **never returned to API clients**. | The diagnosis above was impossible from the recorded data | `test_daily_quota_is_not_retryable_and_carries_sanitized_diagnostics`, `test_per_minute_429_stays_retryable_rate_limit` |
| New `provider_quota_exhausted` (HTTP 429, **not retryable**) when the quota id is per-day or the retry delay is at least 1 h. The extension explains it and hides "Try again". | Users were told "try again shortly" for a 22-hour block | the backend tests above; `App.test.tsx` "explains a used-up provider quota…" |
| `redact_secrets()` masks Google API keys (`AIza…`, `AQ.…`), OAuth tokens and `key=` parameters in diagnostics | Provider messages are logged and written to reports | `test_redaction.py` |
| Harness: failed stages keep their diagnostics and get a separate "Failed stages" table, excluded from statistics. Adds `--only` to retry selected cases and structured log output, and records the extraction `context`. | Separate failures from results; retry only failed cases | `test_failed_stage_keeps_diagnostics_and_is_listed_separately` |
| Harness: a claim taken from the video is verified **with its extraction context**, as the extension does | The harness was not testing the product path for context-dependent (e.g. political) claims | `test_exploratory_case_verifies_extracted_claim`, `test_run_case_and_report` |

Test status: backend 115 passed (ruff, format and mypy clean); extension 36 passed
(typecheck and build); mocked browser E2E smoke passed (12/12).

**Credential hygiene.** The key is kept only in the git-ignored `.env`
(mode 600). The plaintext scratch copy was deleted. A scan found **no**
occurrence of the key in `eval/runs/`, logs, reports, source, docs or any
commit on any fetched branch. Run artefacts (`eval/runs/`) stay git-ignored.
This report contains only sanitized figures.

## 6. Three most important technical improvements

1. **Serving model and quota.** Today one server-side key serves every user. On the free tier that is
   20 requests per model per day, or about 4 full verifications per day *in total*. A public extension needs:
   - a hosted backend with per-user authentication and quotas, plus a hard daily spend cap on a **billed**
     key, or a bring-your-own-key mode;
   - quota exhaustion surfaced explicitly (done in this round).

   Also: the SDK still retries 429s internally (up to 2 extra attempts, at most 8 s backoff), which is
   futile for daily quotas.
2. **Extraction latency and stability.** Median 31 s and up to 88 s, with thinking tokens making up most
   of the output and cost. The same request returned different claims and claim types. Next steps:
   - A/B a low thinking level for extraction (and for analysis and evaluation) against **labelled** moments;
   - consider returning a short list of candidate statements around *t*, so the user picks one instead of
     editing a single guess.
3. **Claim typing and context.** Add explicit handling for:
   - attributed statements ("X said Y": verify the attribution, not Y);
   - religious and philosophical theses (→ `not_verifiable`);
   - political claims, which should carry **speaker and date** from the extraction context into retrieval
     and evaluation, so they are judged against what was true when said.

   Measure all of this on a labelled set (§8) before tuning prompts.

## 7. GO / NO-GO for a public Chrome extension

**NO-GO.**

| Gate | Status |
|---|---|
| Clip-limited video cost | ✅ evidence from 7/7 requests on 2 videos (one model) |
| Live verification works end to end | ⛔ 0/6 completed (quota) |
| Sources trustworthy and verdicts supported | ⛔ unmeasured |
| Extraction accuracy on labelled moments | ⛔ no labels yet |
| Latency acceptable for an in-video side panel | ⛔ median 31 s for extraction alone |
| Capacity and abuse controls for public traffic | ⛔ localhost backend, single shared key, free-tier quota |
| Real youtube.com DOM | ⛔ still blocked here (the proxy denies `www.youtube.com`) |

The architecture's *core* (adapter boundary, clip-limited extraction, mandatory confirmation,
grounding-only citations, deterministic guardrails) is not what blocks launch. **Re-evaluate** after:

- verification has run on all six cases, with the source-review sheet completed by hand;
- 30+ labelled moments exist (EN and RU);
- extraction latency is under about 10–15 s;
- a hosted backend with per-user limits and a billed key is in place.

## 7b. First end-to-end fact-check attempt (2026-10-13, later the same day): not completed

The goal was one complete grounded fact-check of `en-debate-1200`, the ABC News debate of
10 September 2024 at about 20:00. Gemini grounding needs 4 requests (extraction, analysis, grounded
search, evaluation): about $0.05–0.09 at the assumed paid prices, $0 on the free tier.

**No claim, verdict or source was produced. Nothing below is a fact-check result.**

`gemini-3.8-flash` (the configured model; the §2 benchmarks were also run on it):

| Time (UTC) | Operation | Attempts | Result |
|---|---|---|---|
| 12:50 | video extraction | 1 (SDK retries off) | 503 `UNAVAILABLE`, "This model is currently experiencing high demand" |
| 12:52 | video extraction | ≤3 (2 SDK retries) | same 503 |
| 13:03 | video extraction | ≤3 | same 503 |
| 14:06 | video extraction | ≤3 | same 503 |

`gemini-3.6-flash` (fallback model, separate from the 3.8 benchmarks; the §2 benchmarks do **not**
apply to it):

| Time (UTC) | Operation | Attempts | Result |
|---|---|---|---|
| 15:58 | availability test (tiny text-only JSON request) | 1 (no retries) | 503 `UNAVAILABLE`, same message |

**Conclusions:**

- The blocker is provider capacity (HTTP 503), not our quota (that would be a 429), not credentials
  and not the video.
- Whether the daily quota has reset is still unknown: a 503 says nothing about quota.
- The manual-claim path cannot get around it, because analysis, grounded search and evaluation use
  the same Gemini models.
- No downstream step ran after a failure, so nothing billable was spent.

**Changes made for the next attempt (tested offline):**

- `YOUVERIFY_FALLBACK_MODEL` (optional): each call is tried once on the fallback model, only when the
  configured model returns 503. Quota and other errors never fall back.
- Every response records the model that actually served each step: extraction `model`; verification
  `models_used` per step (analysis, retrieval, evaluation). Live reports show both, so 3.6 and 3.8
  results cannot be mixed up.

**Next attempt** (one run, no loop):

```bash
YOUVERIFY_EXTRACTION_MODEL=gemini-3.6-flash YOUVERIFY_VERIFICATION_MODEL=gemini-3.6-flash \
YOUVERIFY_MAX_RETRIES=1 YOUVERIFY_RESOLVE_REDIRECTS=false \
python scripts/live_validation.py eval/live_cases.jsonl --only en-debate-1200 \
    --skip-clipping --verify-from extracted --fetch-sources \
    --price-in 0.75 --price-out 3.75 --price-search-1k 14
```

Alternatively keep 3.8 as primary and set `YOUVERIFY_FALLBACK_MODEL=gemini-3.6-flash`.

## 8. Remaining steps

1. Re-run the failed stages (§4 command) on a billed key or after the free-tier reset, then fill in the
   source review sheet in `eval/runs/<run>/report.md` by hand.
2. Label the six moments: watch each, write down the exact words before *t*, and set `verified_by`. Then
   grow to 30–50 moments and run `scripts/run_eval.py`. Only then report accuracy and F1.
3. Run the thinking-level A/B (improvement 2) on the labelled set.
4. Run the real-youtube.com browser test where YouTube is reachable: `npm run e2e:live`, plus the
   manual checklist in the README.

---

## Appendix A: docs audit (2026-10-12)

Superseded where §3 has live evidence. Clipping is now *observed* to be honoured on
`gemini-3.8-flash`, and the model is confirmed available to this key.


| Topic | Current documentation / evidence | Our implementation | Verdict |
|---|---|---|---|
| API surface | `generateContent` still supported. Interactions API is the newer path but **does not support `video_metadata` (clipping/fps)** | `client.aio.models.generate_content` | Correct; no rewrite |
| YouTube input | Public URL as `file_data.file_uri`; public videos only; free tier 8 h/day | Canonical watch URL from a validated ID | Correct |
| Static vs agentic | Static (default) samples frames at fixed fps. Agentic (Interactions, opt-in, 3.8/3.6 Flash, 3.5 Flash-Lite) navigates the timeline; offsets/fps don't apply | Static with clip offsets | Correct for short windows. Agentic is a fallback if clipping fails |
| Clipping | Documented via `videoMetadata.start_offset/end_offset` · **public report of offsets ignored for YouTube on 3.7 Flash** ([video-intel#141](https://github.com/dzivkovi/video-intel/issues/141)) | Same fields | **Unresolved risk; must be measured** |
| Media resolution | Gemini 3 video frames: 70 tokens (`low`/`medium`), 280 (`high`) | `low`, fps 0.5 | Correct; docs corrected (previously said ~66) |
| Model | `gemini-3.8-flash` is listed as GA. Third-party price: $0.75/$3.75 per 1M tokens (intro through 2026-12-31) | Default model | Plausible; **unconfirmed against the live model list** |
| Search grounding | `uri` is a `vertexaisearch` redirect and `title` is often a bare domain. Gemini 3 bills **per executed search query** (5k/month free, then $14/1k on Google Cloud's pricing page) | Redirect resolved without following; domain falls back to title | Parsing correct · **cost accounting was wrong** (fixed, Appendix B) |
| Search + JSON schema in one call | Not confirmed by any source | Not used: grounded search returns free text, and evaluation is a separate tool-less JSON call | Correct; avoids the unknown |

## Appendix B: defects fixed on 2026-10-12

| Defect | Impact | Fix | Test |
|---|---|---|---|
| Cost estimate ignored grounding search fees, which are billed per query on Gemini 3 | Under-reported cost per verification, potentially by a large factor, because each verification can run several billable searches | `usage.search_queries` + `YOUVERIFY_PRICE_PER_1K_SEARCHES` included in `estimated_cost_usd` | `test_cost_includes_executed_search_queries` |
| `search_queries` fell back to *planned* queries when grounding reported none | Would overcount billable searches and misreport what was searched | Report only queries Google says it executed | `test_planned_queries_are_not_reported_as_executed` |

(At the time nothing had run live; the live findings are in §2–§4 above.)

