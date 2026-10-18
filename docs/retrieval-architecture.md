# Evidence retrieval architecture (updated 2026-10-13, round 2)

**Status:** implemented and covered by offline tests:

- the provider abstraction, with Brave (recommended for public deployment) and SearXNG adapters
  and Gemini grounding as an alternative;
- paid-search cost controls;
- failover and outage semantics;
- ranking, page-text evidence and citation validation;
- source-independence grouping.

§9 lists the reliability defects found and fixed in round 2. §8 gives the shortest path to one real
end-to-end fact-check.

**Not measured yet:** no live comparison of SearXNG, Brave and Gemini
grounding has run. This environment's network policy blocks every search
engine and publisher site (and `api.search.brave.com`). There is no Brave key,
no Docker daemon to host SearXNG, and the Gemini key's free-tier quota is used
up until the daily reset. §6 has the one command that produces the comparison
once those exist.

## 1. Review of the existing retrieval

| Step | Before | Issue for a public product |
|---|---|---|
| A′ analysis | Gemini JSON call: claim type plus 2–4 search queries | fine; queries are reusable by any search backend |
| C retrieval | **One** Gemini call with the Google Search tool; sources = `grounding_chunks`, excerpts = `grounding_supports` | single vendor; the free tier is capped at 20 requests/day/model (live run, `live-validation-report.md` §4); excerpts are **model summaries**, not page text, so a citation cannot be checked against its page; grounding search is billed per executed query |
| D–E evaluation | Gemini JSON call over numbered sources | fine; independent of where sources come from |
| F guardrails | Python: drop unknown ids, downgrade unsupported verdicts | fine |

`VerificationService` called `llm.grounded_search` directly, so retrieval was
not swappable. Everything else in the pipeline already worked on plain
`EvidenceSource` objects. That made a single seam at step C the smallest
change that enables a comparison.

## 2. What changed (smallest increment)

```
analysis (Gemini) ─► Retriever ─► evaluation (Gemini) ─► guardrails
                       │
                       ├─ GeminiGroundingRetriever   (original behaviour, still the default)
                       └─ SearchRetriever
                            FailoverSearch[SearchProvider…]   default: brave only (ordered, breaker,
                                                              daily spend cap for paid providers)
                            rank_hits      canonical-URL dedupe · rank fusion across queries ·
                                           lexical relevance filter · ≤2 results per site
                            fetch_page     DNS-pinned, SSRF-guarded, size-capped, redirects re-validated
                            extract_passages  verbatim ≤400-char passages, checked against the page
```

| File | Role |
|---|---|
| `services/retrieval.py` | `Retriever` protocol, `RetrievalResult`, `GeminiGroundingRetriever`, `SearchRetriever` |
| `services/search.py` | `SearchProvider` protocol, `SearxngProvider`, `BraveProvider`, `FailoverSearch` (cooldown breaker) |
| `services/evidence.py` | canonical URLs, site grouping, rank fusion, relevance, passage extraction, verbatim check |
| `services/fetch.py` | server-side page fetching with SSRF guards |
| `evaluation/retrieval.py`, `scripts/compare_retrieval.py`, `eval/retrieval_cases.jsonl` | side-by-side comparison on fixed queries |

**What is preserved:**

- All endpoints, request and response shapes, and the default behaviour.
  `YOUVERIFY_RETRIEVAL_BACKEND` defaults to `gemini_grounding`.
- Every pre-existing test passes unchanged.

**What is additive:**

- `SourceRef.excerpt_origin` (`grounding_summary` | `page_text` | `search_snippet`) on each source.
  The side panel now labels excerpts truthfully instead of always calling them AI summaries.
- `/health` reports `retrieval_backend`.
- The evaluator prompt tells the model where each excerpt came from.

**Deliberately not built yet** (§5): Redis, per-user identity, per-user quotas,
background jobs, LLM re-ranking. A per-process daily cap on *paid search* exists (§2, "Paid search").

### Citation validation, by origin

| Origin | How it is produced | What is guaranteed |
|---|---|---|
| `page_text` | cut from the fetched page | the excerpt is a verbatim substring of that page (checked before use); the URL is the final URL after validated redirects |
| `search_snippet` | the page could not be fetched (timeout, 4xx/5xx, non-HTML) | the URL came from a search result; the snippet is the engine's text and is labelled as such for the evaluator and the user |
| `grounding_summary` | unchanged grounding path | URL from grounding metadata only; text is a model summary (labelled) |

In every mode the model still cannot introduce a URL: sources come only from
search results or grounding metadata, and the existing guardrails apply unchanged.

### Paid search policy

- **Default provider list:** `brave`. Gemini grounding (`YOUVERIFY_RETRIEVAL_BACKEND=gemini_grounding`,
  still the default backend) and SearXNG (`YOUVERIFY_SEARCH_PROVIDERS=searxng`) remain independently
  selectable.
- **Configuration is refused** (`backend_misconfigured`, 503) when any of these hold:
  - a metered provider has no price (`YOUVERIFY_PRICE_PER_1K_BRAVE`);
  - paid search is configured with no daily cap (`YOUVERIFY_SEARCH_DAILY_BUDGET_USD`);
  - a paid provider sits after another provider, i.e. an automatic paid fallback, unless
    `YOUVERIFY_ALLOW_PAID_SEARCH_FALLBACK=true`.
- **The cap is enforced per request:** spend is reserved before each paid request and refunded if the
  request fails. When the day's cap is reached, the paid provider is skipped (`budget_exhausted`), and
  the user gets a non-retryable `retrieval_unavailable` ("daily evidence-search capacity reached").
  The cap resets at 00:00 UTC.
- **The cap is per process.** With N instances, give each 1/N of the budget until the counter moves to
  Redis.

### Failover and outage semantics

1. Providers are tried in `YOUVERIFY_SEARCH_PROVIDERS` order. Listed but unconfigured providers are
   skipped.
2. A provider error moves to the next provider. Error types: timeout, network error, 429, HTTP ≥ 400,
   invalid JSON, cooling down, budget exhausted.
3. An empty result also moves to the next provider, but does not count as a failure.
4. After `YOUVERIFY_SEARCH_FAILURES` (default 3) consecutive failures, a provider is skipped for
   `YOUVERIFY_SEARCH_COOLDOWN_SEC` (default 60 s).
5. SearXNG's `unresponsive_engines` are recorded as `degraded_engines`.
6. **Outage vs. no evidence:**

   | Situation | Result |
   |---|---|
   | every query failed on every provider | error `retrieval_unavailable` (503, retryable; not a verdict) |
   | results found, but **no** page could be fetched and none had a snippet | error `retrieval_unavailable` |
   | grounding ran no search (no grounding metadata) | error `retrieval_unavailable` |
   | search ran and returned **zero** results | verdict `insufficient_evidence` ("The search returned no results for this claim.") |
   | results found, none relevant to the claim | verdict `insufficient_evidence` |
   | pages read, no passage about the claim | verdict `insufficient_evidence` |
   | some queries failed, others succeeded | normal evaluation on what was retrieved; failures in diagnostics |

   `insufficient_evidence` therefore always means that retrieval worked and the evidence did not
   settle the claim. There is **no silent fallback** to Gemini grounding.
7. Search results are cached per query and language (`YOUVERIFY_CACHE_TTL_SEC`). Cache hits are not
   billed.

### Independence

`evidence.origin_groups` puts sources in one *origin group* when either holds:

- they share a registrable site (e.g. `edition.cnn.com` and `cnn.com`);
- their publisher text is a near-copy: word 5-gram containment ≥ 0.6, with at least 8 shingles. This
  catches syndicated wire stories and republished press releases.

Grounding summaries are only grouped by site, since their wording is the model's own.

The groups are used in three places:

- `independent_source_count` counts origin groups among the sources the evaluator found relevant;
- the "single source domain or original report" caution applies when the verdict's basis is one group;
- the evaluator is told which sources share a site or report.

**Limits:** near-copy detection needs overlapping excerpts. Two outlets *paraphrasing* the same wire
story are still counted as independent. Detecting that would need an LLM or citation-graph check,
which is not built.

### Evidence supports the verdict

The deterministic checks after evaluation are:

- the verdict must be backed by at least one source the evaluator assessed in that direction
  (unchanged);
- **new:** a key finding may only cite sources whose assessed stance is compatible with the finding.
  Other citations are removed and counted in `guardrail_notes`;
- **new:** a decisive verdict that rests only on search snippets is flagged.

These checks make the output internally consistent. They do **not** prove the evaluator read the
sources correctly. That is what the human review sheet measures.

## 3. SearXNG: assessment and limitations

SearXNG is a self-hostable meta-search engine. It is free per query and keeps
claims away from a single vendor, which makes it worth evaluating as the
primary provider. It should **not** be assumed to stay reliable at scale:

| Limitation | Consequence | Mitigation here / needed later |
|---|---|---|
| It **scrapes** upstream engines (Google, Bing, DuckDuckGo, Brave web…) rather than using licensed APIs | Upstream engines rate-limit, CAPTCHA or block a single server IP as volume grows; results then thin out or vanish without an error | breaker and failover to Brave; `degraded_engines` logged. Later: alert on degraded-engine rate |
| Upstream terms of service generally prohibit automated querying | Legal and commercial risk for a public product | **Open product/legal decision.** Use a licensed API (Brave) as the primary for a public launch unless the risk is accepted |
| Scrapers break when upstream HTML changes | Silent quality drops after upstream redesigns; requires keeping the instance updated | pin and monitor the image; the comparison harness doubles as a regression check |
| Results depend on which engines answered | Non-deterministic evidence sets; harder to reproduce a verdict | record the provider and degraded engines per request (done) |
| Public SearXNG instances | Rate-limited, unreliable, may log queries | **never** use public instances; only a private one on the backend network |
| Configuration | JSON output is off by default; the bot limiter must allow the backend | set `search.formats: [html, json]`; reach it only from the backend (private network) |
| Operations | Another service to host, update and monitor | acceptable for evaluation; reconsider before launch |

**Brave Search API:** an official, licensed API with predictable behaviour and
a per-request price. Set the price with `YOUVERIFY_PRICE_PER_1K_BRAVE`, using
the current price list; none is assumed. If Brave is used with no price
configured, the cost is reported as **unknown**, not $0.

**Gemini grounding:** kept as an alternative backend for comparison. It has the
fewest moving parts and the best-known index, but excerpts are summaries and
the free tier is too small to serve the public.

## 4. Credentials and users

- Search keys (`BRAVE_SEARCH_API_KEY`) and `GEMINI_API_KEY` exist **only in the backend environment**.
  The extension holds no secrets: it calls the backend URL only (`extension/src/shared/config.ts`).
- Keys are `SecretStr`, are sent only in request headers (never in URLs), and pass through
  `redact_secrets` in diagnostics. Tests assert the Brave key never appears in a URL or error.
- Users need **no keys, models or local installs** once the backend is hosted. For a public build,
  set `VITE_BACKEND_URL` to the hosted HTTPS URL, list it in `host_permissions`, and restrict CORS to
  the published extension ID.

## 5. Path to server-side deployment (design only; not built)

| Concern | MVP today | Before public launch |
|---|---|---|
| Hosting | localhost | one stateless FastAPI service behind HTTPS (several instances) |
| Shared cache | per-process `TTLCache` (`get`/`set`) for extraction, verification and search results | Redis behind the same interface; key extraction by (video, window), verification by (normalized claim, language, backend) |
| Per-user limits | per-IP sliding window | anonymous per-install token issued on first run, plus per-IP limits; Redis counters |
| Cost controls | cost estimate per response; **per-process daily cap on paid search** (refuses to start paid search without one) | the same cap shared via Redis; a cap on Gemini spend too; per-user daily quota; cached results first |
| Search reliability | breaker per process | per-process breakers are acceptable; add health checks and alerting on failover rate |
| Secrets | `.env` | platform secret store; separate keys per environment; rotation |
| Fetching | in-process, DNS-pinned, SSRF checks per hop, bounded decompression | also block private ranges at the network egress (defence in depth); if egress goes through a proxy, enforce the policy there |

## 6. Measuring (not yet run)

`eval/retrieval_cases.jsonl` holds 12 claims (8 EN, 4 RU) with fixed queries, so every backend gets
identical input. Six of them come from the two evaluation videos and carry `source_video` (watch URL
with timestamp) and `context` (speaker, event, date):

- **Political attributions from the debate** keep speaker and date. The evidence must be about the
  10 September 2024 debate, not about today.
- **Religious and philosophical cases** are split into checkable parts:
  - what a text says (Mark 15:21);
  - who wrote what (the Pelevin attribution).

  The untestable thesis ("religions are anaesthesia") is noted as such and not searched.
- **Labels:** `draft_verdict` is the author's expectation, **not ground truth**, until `verified_by` is
  set. One case has no draft at all, because the author does not know whether the Pelevin quote is
  authentic.

```bash
python scripts/compare_retrieval.py eval/retrieval_cases.jsonl \
    --backends brave,gemini_grounding --check-citations \
    --price-brave-1k <current price> --search-budget-usd 1 \
    --price-in 0.75 --price-out 3.75 --price-search-1k 14
# add searxng with --searxng-url http://localhost:8888 to include it
```

The script builds each backend through the same validated factory as the API. A paid backend without
a price and cap refuses to start, before any request is made.

**Cost of one full run:** 12 claims × 2 queries = 24 Brave requests, plus 12 Gemini grounding requests.
The grounding requests count against the Gemini quota; run `--backends brave` alone to spend none.

**Measured automatically, per backend (successful runs only; failures are counted with their codes):**

| Metric | Meaning |
|---|---|
| latency | median retrieval time, including page fetching |
| coverage | claims with ≥1 source; sources per claim |
| independence | independent sites per claim; independent **origins** per claim (sites and republished copies count once) |
| evidence type | page-text share; source-type mix (domain heuristic, not a reliability verdict) |
| relevance (proxy) | mean lexical overlap of excerpts with the claim |
| citation accuracy (`--check-citations`) | `verbatim` (page text, checked during retrieval) / `consistent` (re-fetched page contains the excerpt's content words and numbers) / `needs_review` / `unreachable` |
| provider failures | count and error codes (`retrieval_unavailable`, `provider_quota_exhausted`, …) |
| cost | search fees plus grounding tokens and searches, per claim |

**Judged by a person** on the review sheet, per source: relevant? reliable? supports, contradicts or
neither? Relevance, reliability and accuracy figures are reported only after that.

## 7. Priority

A trustworthy end-to-end fact-check comes before deployment work: §8 first, then the comparison,
then §5.

## 8. Shortest path to ONE real end-to-end fact-check

Success criterion: YouTube claim → correct extraction → independent web evidence → defensible verdict
→ working citations.

**Exact blockers in the development environment (2026-10-13):**

| Needed | Status |
|---|---|
| Gemini requests | key present, but the free-tier cap of 20 requests/day/model was used up on 2026-10-13 (resets about 00:00 UTC) |
| Brave Search | no `BRAVE_SEARCH_API_KEY`; `api.search.brave.com` is denied by the network policy |
| Publisher pages (Brave path page fetching, citation checks) | denied by the network policy |
| youtube.com (only for the browser test) | denied; not needed for the backend, since Gemini fetches the video itself |

**Path A, no new credentials (Gemini grounding).** Run after the quota resets. It costs 2 Gemini
requests for extraction and analysis, 1 for grounded search and 1 for evaluation: **4 requests in
total**.

```bash
python scripts/live_validation.py eval/live_cases.jsonl --only en-debate-1200 \
    --skip-clipping --verify-from extracted --fetch-sources
```

This reaches a verdict with grounding-attributed citations. The automatic citation check needs
publisher pages, so here a person clicks each link and fills in the review sheet. Excerpts are model
summaries, which is the weaker kind of citation.

**Path B, recommended (Brave).**

Set up the environment:

- add the `BRAVE_SEARCH_API_KEY` environment secret;
- allow `api.search.brave.com` and general web access for page fetching in the environment's network
  settings;
- set `YOUVERIFY_RETRIEVAL_BACKEND=search`, `YOUVERIFY_PRICE_PER_1K_BRAVE` and
  `YOUVERIFY_SEARCH_DAILY_BUDGET_USD`.

Then run the same command. It costs **3 Gemini requests** (extraction, analysis, evaluation) and
about 2–4 Brave requests. The evidence is verbatim page text, so citations are checked automatically.

**Choice of case:** `en-debate-1200`, a factual political claim with a clear time and speaker context.
Nirvana cases are mostly opinion or attribution and are the second step. In both paths a person must:

1. watch the moment and confirm the extracted sentence and speaker;
2. review each cited source;
3. record the result with `verified_by`.

Only then is it a validated end-to-end fact-check.

## 9. Reliability review, round 2 (defects found and fixed)

| # | Defect | Fix | Test |
|---|---|---|---|
| D1 | All providers failing produced `insufficient_evidence` | `retrieval_unavailable` error (503, retryable) | `test_all_providers_down_is_retrieval_unavailable_not_insufficient_evidence` |
| D2 | All pages unreadable (no snippets) produced `insufficient_evidence`; grounding with no search too | same error, `stage: fetch` / `no_grounding_metadata` | `test_every_page_unreadable…`, `test_grounding_without_search_metadata…` |
| D3 | `searxng,brave` default meant automatic paid fallback; no spend limit | default `brave`; price and daily cap required; paid fallback needs opt-in; `SpendGuard` reserve/refund | `test_paid_search_requires_price_and_daily_cap`, `test_paid_fallback_needs_explicit_opt_in`, `test_spend_guard…`, `test_exhausted_budget_is_not_retryable` |
| D4 | DNS checked, then resolved again by the HTTP client (rebinding) | connect to the checked IP; Host header and TLS SNI keep the name; every hop re-pinned; any private address in the answer blocks | `test_connection_is_pinned…`, `test_any_private_address…`, `test_redirect_hop_is_resolved_and_pinned_again` |
| D5 | gzip decompressed before the size cap (bomb) | `Accept-Encoding: identity`; raw cap; bounded `zlib` decompression; other encodings rejected | `test_gzip_bomb_is_bounded`, `test_unsupported_content_encoding…` |
| D6 | No `<meta charset>` sniffing: legacy Russian pages became junk | header charset, else meta sniff, else UTF-8 | `test_meta_charset_is_honoured…` |
| D7 | Independence counted domains; syndicated copies counted twice | origin groups (site + near-copy text) for counts, cautions and the evaluator prompt | `test_syndicated_copies…`, `test_republished_reports_count_once…` |
| D8 | Key findings could cite sources assessed against them | stance-compatible citations only; removals noted; snippet-only verdicts flagged | `test_findings_cannot_cite…`, `test_snippet_only_verdict…` |
| D9 | Video context never reached the evaluator; no rules for religious, philosophical or attributed claims | context and an "as said at the time, keep attributions" rule in evaluation; claim-type rules in analysis | `test_snippet_only_verdict_is_flagged_and_context_reaches_the_evaluator` (prompt effect itself unmeasured) |
| D10 | `live_validation.py` ignored the configured retrieval backend | built once per run via the API's validated factory | `test_run_case_uses_the_configured_retriever` |
