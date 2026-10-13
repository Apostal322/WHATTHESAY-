# Evidence retrieval architecture (2026-10-13)

**Status:** the provider abstraction, SearXNG and Brave adapters, failover,
ranking, page-text evidence and citation validation are implemented and
covered by offline tests.

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
                            FailoverSearch[SearchProvider…]   searxng → brave (ordered, breaker)
                            rank_hits      canonical-URL dedupe · rank fusion across queries ·
                                           lexical relevance filter · ≤2 results per site
                            fetch_page     SSRF-guarded, size-capped, redirects re-validated
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

**Deliberately not built yet** (§5): Redis, per-user identity, budgets,
background jobs, LLM re-ranking.

### Citation validation, by origin

| Origin | How it is produced | What is guaranteed |
|---|---|---|
| `page_text` | cut from the fetched page | the excerpt is a verbatim substring of that page (checked before use); the URL is the final URL after validated redirects |
| `search_snippet` | the page could not be fetched (timeout, 4xx/5xx, non-HTML) | the URL came from a search result; the snippet is the engine's text and is labelled as such for the evaluator and the user |
| `grounding_summary` | unchanged grounding path | URL from grounding metadata only; text is a model summary (labelled) |

In every mode the model still cannot introduce a URL: sources come only from
search results or grounding metadata, and the existing guardrails apply unchanged.

### Failover behaviour

1. Providers are tried in `YOUVERIFY_SEARCH_PROVIDERS` order (default `searxng,brave`). Unconfigured
   providers are skipped.
2. A provider error moves to the next provider. Error types: timeout, network error, 429, HTTP ≥ 400,
   invalid JSON.
3. An empty result also moves to the next provider, but does not count as a failure.
4. After `YOUVERIFY_SEARCH_FAILURES` (default 3) consecutive failures, a provider is skipped for
   `YOUVERIFY_SEARCH_COOLDOWN_SEC` (default 60 s), then tried again. A dead SearXNG instance therefore
   costs at most 3 timeouts per minute, not one per request.
5. SearXNG's own `unresponsive_engines` (upstream engines that failed, e.g. CAPTCHA) are recorded as
   `degraded_engines` in diagnostics.
6. If every provider fails, the verdict is `insufficient_evidence` with the reason *"All search
   providers failed or returned no results."* There is **no silent fallback** to Gemini grounding:
   a mis-configured backend returns `missing_credentials` (503) instead.
7. Search results are cached per query and language (`YOUVERIFY_CACHE_TTL_SEC`). Cache hits are not
   billed.

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
| Cost controls | cost estimate per response | global daily spend cap per provider (deny with a clear error when reached); per-user daily quota; cached results first |
| Search reliability | breaker per process | per-process breakers are acceptable; add health checks and alerting on failover rate |
| Secrets | `.env` | platform secret store; separate keys per environment; rotation |
| Fetching | in-process with SSRF checks | also block private ranges at the network egress (closes the DNS-rebinding gap noted in `fetch.py`) |

## 6. Measuring (not yet run)

`eval/retrieval_cases.jsonl` holds 10 claims (7 EN, 3 RU): science, history,
politics, a religious-text claim, and literature. Each case has fixed queries,
so every backend gets identical input. `draft_verdict` is the author's
expectation, **not ground truth**, until someone fills in `verified_by`.

```bash
# private SearXNG with JSON enabled, e.g. on localhost:8888
python scripts/compare_retrieval.py eval/retrieval_cases.jsonl \
    --backends searxng,brave,gemini_grounding --searxng-url http://localhost:8888 \
    --price-brave-1k <current> --price-in 0.75 --price-out 3.75 --price-search-1k 14
```

**Measured automatically, per backend (successful runs only; failures are counted separately):**

- median retrieval latency, including page fetching;
- sources per claim and independent sites per claim;
- page-text share;
- source-type mix (a domain heuristic, not a reliability verdict);
- mean lexical excerpt relevance (a proxy);
- cost per claim.

**Judged by a person** on the review sheet: is each source relevant, is it
reliable, and does it support or contradict the claim? Relevance and
reliability figures are only reported after that sheet is filled in.

**What this session needs to run it:**

- network access to the search engines (for SearXNG) or to `api.search.brave.com`;
- a Brave key as an environment secret;
- a host for SearXNG;
- Gemini quota for the grounding column.

## 7. Priority

A trustworthy end-to-end fact-check comes before deployment work:

1. Run the comparison on the 10 cases and fill in the review sheet.
2. Choose the primary backend from the measured relevance, reliability and latency, plus the legal
   decision on SearXNG.
3. Complete the six live video cases end to end on that backend (`live-validation-report.md` §4).
4. Then build the §5 items.
