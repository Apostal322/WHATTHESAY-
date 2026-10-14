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

## 7c. Second end-to-end attempt (2026-10-14 02:55–03:22 UTC): extraction and analysis live; grounding quota-blocked

**Budget:** at most 10 model calls and $1. **Used:** 8 calls.

| Calls | Breakdown |
|---|---|
| Successful generations | 2 (extraction, analysis) |
| Rejected with 503 | 5 |
| Rejected with 429 | 1 |
| Estimated spend on success | about $0.01 at assumed prices |

Gemini 3.6 and 3.8 are reported separately, and none of the §2 (3.8) benchmarks apply to 3.6.

| UTC | Step | Model | Result |
|---|---|---|---|
| 02:55:31 | video extraction | gemini-3.8-flash | 503 `UNAVAILABLE` (high demand) |
| 02:55:31–40 | video extraction | **gemini-3.6-flash** (fallback) | **success**: 9.7 s, 3,576 in / 1,246 out tokens (incl. thinking), ≈ $0.0074 |
| 02:55:40 | analysis | 3.8 → 3.6 fallback | 503, 503 |
| 02:56:52 | analysis (`verify_claim.py`, saved extraction) | 3.6 → 3.8 fallback | 503, 503 |
| 03:22:0x | analysis | **gemini-3.6-flash** | **success** (output not kept: defect D-B below) |
| 03:22:09 | grounded search | gemini-3.6-flash | **429 `RESOURCE_EXHAUSTED`** "You exceeded your current quota…", with no machine-readable quota detail |

**What the extraction produced** (model output; nothing here is human-verified):

- **Window:** 1155–1210 s of https://www.youtube.com/watch?v=VgsC_aBquUE.
- **Heard (model transcription, flagged exact by the model, unverified):**
  > "In your home state of Florida, you surprised many with regard to your six-week abortion ban because
  > you initially had said that it was too short and you said quote I'm going to be voting that we need
  > more than six weeks, but then the very next day you reversed course and said you would vote to
  > support the six-week ban."
- **Speaker (model inference, unverified):** moderator Linsey Davis, questioning Donald Trump.
- **Claim (model):** Trump first said Florida's six-week ban was too short and that he would vote for
  more than six weeks, then the next day said he would vote to support the six-week ban.

**Reviewer notes, to check by hand:**

1. Confirm the wording and speaker against the video or an authentic transcript.
2. The claim is the moderator's *characterisation*. The checkable facts are what Trump said in late
   August 2024 about Florida's six-week ban and Amendment 4. "Vote to support the six-week ban" may
   compress "vote against Amendment 4", so the evaluator should treat wording precision as a scope
   issue.
3. Evaluate against what was known in September 2024 (temporal context).

**Result:** no verdict. This is **not** a completed fact-check (not Tier 3).

**Blockers:**

- **Model capacity:** both Flash models return 503 intermittently; about 1 in 3 calls succeeded in this
  window.
- **Grounding quota:** Google Search grounding on this key now returns 429 without quota details.
- **No other retrieval path here:** Brave has no key, and api.search.brave.com and publisher sites are
  blocked by the sandbox network policy.

**Artifacts:**

- `eval/factchecks/en-debate-1200-20261014T025653Z.{json,md}` (analysis 503s);
- `eval/factchecks/en-debate-1200-20261014T032209Z.{json,md}` (grounding 429; recorded with the pre-fix
  code `provider_rate_limited`).

Both carry the full extraction for review.

**Defects found by this run and fixed:**

| # | Defect | Fix | Test |
|---|---|---|---|
| D-A | A 429 saying "exceeded your current quota" without `QuotaFailure` detail was classified `provider_rate_limited` ("try again shortly", retryable), inviting futile retries | treated as `provider_quota_exhausted` (non-retryable); a bare 429 without quota wording stays retryable | `test_quota_message_without_details_is_not_retried_as_a_rate_limit`, `test_bare_429_without_quota_wording_stays_a_retryable_rate_limit` |
| D-B | When verification failed after analysis, the completed analysis (normalized claim, type, planned queries), models and tokens were discarded | error diagnostics (operator-only, never sent to clients) carry `completed`: analysis, retrieval summary, models_used, usage; the review sheet shows it | `test_partial_progress_survives_a_later_failure` |

**To finish Tier 3, one of the following:**

- **(a) Gemini grounding when quota allows:** about 2 calls (grounding and evaluation) plus re-analysis,
  so 3 calls:

  ```bash
  YOUVERIFY_VERIFICATION_MODEL=gemini-3.6-flash YOUVERIFY_FALLBACK_MODEL=gemini-3.8-flash YOUVERIFY_MAX_RETRIES=0 \
  python scripts/verify_claim.py --from-run eval/factchecks/en-debate-1200-20261014T032209Z.json \
      --price-in 0.75 --price-out 3.75 --price-search-1k 14
  ```

  This reuses the committed artifact's extraction, so no new extraction call is made.
- **(b) Brave search mode:** `BRAVE_SEARCH_API_KEY`, network access to api.search.brave.com and
  publisher sites, a price, and a daily budget. Then 2 Gemini calls (analysis and evaluation) plus 2–4
  Brave requests, with verbatim page citations.

Then a person fills in the review section of the sheet.

## 7d. Reference record and third attempt (2026-10-14 09:45–10:07 UTC)

- **Sources supplied by the owner**: ABC transcript, Reuters, Florida DOS Amendment 4 record, Roll Call
  Factbase transcript.
  - None is reachable from the development environment: sandbox curl is refused, and the fetch tool
    gets DNS failures or a block.
  - They are recorded as `user_supplied`, **not assessed**, and never treated as application-retrieved
    evidence.
- **Reference record** `eval/reference/en-debate-1200.{json,md}` (schema `app/evaluation/reference.py`):
  - the moderator's claim is split into P1 (Trump called six weeks too short), P2 (he would vote no on
    Amendment 4), P3 ("the very next day") and P4 ("vote to support the six-week ban", a
    characterization);
  - reference verdict **supported** (acceptable: supported, mixed);
  - evidence stances come from **web-search summaries** of six outlets;
  - review status `ai_reviewed`, so any score against it is **provisional** and never counted as
    accuracy.
- **Pipeline:** 2 bounded attempts at verification, 3 calls in all: 09:46 (3.6 and 3.8, both 503) and
  10:06 (3.6, 503). No verdict; nothing billed.
- **Code:**
  - `verify_claim.py` now scores each run against `eval/reference/<id>.json` when one exists, marked
    PROVISIONAL unless human-verified;
  - 13 offline regression tests pin the case's shape. They check attribution versus characterization,
    that the speaker and date context reach both prompts, that the syndicated report counts once, that
    a confident `contradicted` without contradicting evidence is downgraded, and that a well-explained
    `mixed` is kept.
- **Remaining blocker:** inference capacity. Every Gemini Flash call today returned 503 except the
  02:55 extraction and the 03:22 analysis. The same claim then needs 2 calls with Brave or the supplied
  pages (analysis, evaluation), or 3 with grounding.

## 7e. Offline evidence fixture and proposition-level checks (2026-10-14, no API calls)

Repeated Gemini 503 retries had stopped paying off, so this round made **no model calls** and spent no
credit. Instead it made the debate case testable without live retrieval.

**What the user supplied.** Two previously unreachable references: Roll Call Factbase's timestamped
debate transcript (statement attributed to Linsey Davis at about 15:55–16:12 of *its* timeline) and the
Florida Department of State's Amendment 4 record, plus the Reuters report again.

**Could the development environment open them? No (measured).** `curl`: HTTP 000 for all three; fetch
tool: DNS failure (rollcall.com, constitutionalinitiatives.dos.fl.gov) or refusal (reuters.com). The new
`scripts/capture_evidence.py --check-only` gives the full picture of the sandbox:

| target | DNS | HTTP |
|---|---|---|
| U1–U5 (ABC, Roll Call ×2, Florida DoS, Reuters) | failed | – |
| www.youtube.com | failed | – |
| api.search.brave.com | failed | – |
| generativelanguage.googleapis.com | ok | 404 (reachable; no key sent) |

Web search returned AI-written summaries of syndicated copies, not page text; they were **not** used as
excerpts.

**Changes.**

| Change | Where | Test |
|---|---|---|
| Reference record: U5 (Roll Call transcript) added; U2/U3 re-supplied with `located_on` and the user's description; per-source `source_type`, `located_by`, `inspected_by_app`, `human_approved`, `stance_basis`; dated events E1–E3 and `claim_date`; P5 (legal effect of a "no" vote, primary source U3) split from P4 (characterization). Validators stop an AI reviewer from marking the record human-verified or a source human-approved | `eval/reference/en-debate-1200.*`, `evaluation/reference.py` | `test_case_en_debate_1200.py` |
| Offline evidence fixture: original URLs, dates, types, anchors, capture metadata (time, method, environment label, page hash), attempt log. All five sources `pending_capture`; no text invented | `eval/fixtures/en-debate-1200/`, `evaluation/fixture.py` | `test_fixture_en_debate_1200.py` |
| `capture_evidence.py`: the app's own SSRF-guarded fetcher and passage windows cut ≤3 verbatim excerpts of ≤400 chars per source; `--html-file` parses a browser-saved page; `--check-only` measures reachability | `scripts/` | capture tests (synthetic pages) |
| `FixtureRetriever`: replays captured excerpts as `page_text`, searches nothing, refuses an uncaptured fixture, and adds a notice to every response that the evidence is an offline fixture | `evaluation/fixture.py`, `retrieval.py`, `verification.py` | pipeline tests |
| Quotation check: quoted words in the explanation or a key finding must occur verbatim in the cited sources' page text; otherwise a guardrail note says to treat them as paraphrase or interpretation (advisory; does not change the verdict) | `verification.py` | quote tests |
| Chronology: "the very next day" checked from event dates; reported (secondary) dates give `consistent_unconfirmed`, never `consistent`; events after the claim date are separated | `evaluation/chronology.py` | chronology tests |
| Proposition runner: the normal pipeline once per proposition, scored per proposition (provisional) | `evaluation/propositions.py`, `scripts/run_fixture_case.py` | runner tests |

The new tests use **synthetic** pages (example.org hosts, text marked SYNTHETIC) to drive the real
capture, retrieval, guardrail and scoring code; they measure no accuracy. Mutation check: disabling the
quotation check fails 4 of them, and disabling the `contradicted` downgrade fails the proposition test.

**Still blocked.** No captured excerpt, no application verdict, no human sign-off. The shortest path is
the [Windows guide](windows-local-validation.md): `--check-only` → capture → `run_fixture_case.py --llm
gemini --max-calls 10` (live evaluation of offline evidence) → a manual-claim run with live retrieval
(`verify_claim.py`; grounding needs quota, or use the Brave search backend).

## 7f. Audit of the fixture work (2026-10-14, later session)

A parallel session had already pushed `c2472dc`: provenance categories, the offline fixture (all
sources `pending_capture`), capture and per-proposition scripts, the quotation guard, the
chronology check and the Windows guide. This session audited it and fixed what it found, with
**no model calls**:

| Finding | Fix | Test |
|---|---|---|
| The quotation guard told users that quoting the **claim under review** was "a paraphrase or an interpretation", which is noise on most results | Quotes that match the claim's own wording get an accurate note ("the wording of the claim being checked … not a direct quotation of the speaker"); other unverified quotes are flagged as before | `test_quoting_the_claim_under_review_gets_an_accurate_note` |
| `FixtureRetriever` took a source's domain from the original URL even when the capture followed a redirect, so citations and independence grouping credited the wrong site | Domain from the captured `final_url` | `test_redirected_capture_is_credited_to_the_site_actually_read` |
| Windows guide: on PowerShell 5.1 it said to paste the key after `=`, which PSReadLine saves in plain text to `ConsoleHost_history.txt`. Its pre-commit check matched only `AIza…`, not the `AQ.…` key format in use | Masked `Read-Host -AsSecureString` for 5.1 and 7, a warning about the history file, and `scripts/scan_secrets.py --staged` | `test_scan_secrets.py` |
| No automated secret scan (ad-hoc greps before each commit) | `scripts/scan_secrets.py`: tracked files (now a CI step) or staged additions; Google key formats, OAuth tokens, private keys, non-empty secret assignments, tracked `.env`; redacted output; `secret-scan: allow` marks deliberate test fakes. Verified to catch the real key format on the git-ignored `.env` (count only, nothing printed) | 11 tests, including a staged-mode run in a throwaway repository and "the repository itself is clean" |

**Network re-check (`scripts/capture_evidence.py --check-only`):** every source host, YouTube and
Brave fail DNS from this environment; only the Gemini API host resolves. The fixture therefore
remains `pending_capture`: no excerpt exists, and none was written by hand.

**Known gap, not fixed:** the quotation guard is advisory. In the P4 regression test, an evaluator
that treats CBS's "vote to uphold" framing as Trump's words still yields a confident `supported`.
The scorer counts that as a false confident verdict and the response carries a guardrail note, but
the verdict is not changed. A deterministic override would need a reliable attribution check, which
is not built.

## 8. Remaining steps

0. On a networked machine: capture the debate fixture and run it proposition by proposition (§7e,
   [Windows guide](windows-local-validation.md) §3, §5, §9).
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

