# Quality audit and hardening report (2026-10-15)

Branch `claude/kind-hawking-oybsqt`, starting from `796fe74`. Companion files:

- [`quality-matrix.json`](quality-matrix.json): 59 checks with status, validation level and evidence, validated by `backend/tests/test_quality_matrix.py`.
- [`defects.md`](defects.md): defect register (H-1 to H-17) and known limitations (L-1 to L-9).
- [`devops-handoff.md`](devops-handoff.md): what deployment must provide.
- [`store-compliance.md`](store-compliance.md): Chrome Web Store checklist (not a compliance claim).

## Summary

The audit found **17 defects**:

| Severity | Count | Meaning |
|---|---|---|
| S1 | 5 | Could produce a wrong verdict, a security exposure or wasted spend in normal use |
| S2 | 7 | Wrong behaviour in a plausible edge case |
| S3 | 5 | Hygiene |

All 17 have regression tests. 15 are fixed in code. The other 2 (H-8 and H-15) are prompt rules
whose effect on the model cannot be measured offline.

The most consequential fixes:

- **Wrong verdict reused across videos (H-1).** The result cache ignored the claim's context.
- **Cancelled requests kept spending money (H-9).** Verified on a real server: the extension's
  Cancel button did not stop the paid video model call.
- **A source assessed both ways could back a confident verdict (H-12).**
- **Prompt-injection gaps (H-2):** in delimiter escaping and in unescaped source titles.
- **Unbounded request bodies (H-4)** when sent chunked.

**What did not improve:**

- **Live validation is still blocked.** One bounded live run on 2026-10-15 got through claim
  analysis, but Google Search grounding returned `429 RESOURCE_EXHAUSTED`.
- **ML accuracy remains unmeasured.** There are no human-reviewed labels.

| Matrix status | Count |
|---|---|
| PASS | 34 |
| FAIL | 6 |
| BLOCKED | 8 |
| NOT TESTED | 10 |
| NOT APPLICABLE | 1 |

## Test report

| Check | Result | Level |
|---|---|---|
| Backend `pytest -q` | **356 passed, 1 xfailed**. The xfail is the known limitation L-1, kept strict. Baseline was 297 | unit, property, mocked integration, real server |
| Property-based tests (`hypothesis`) | 8 invariants: guardrails, citations, delimiter escaping, IPv4 encodings, canonical URLs, spend guard, rate limiter, video window. They found 2 defects (H-13) | property |
| Extension `vitest` | **46 passed**. Baseline was 41. Includes 2000 seeded random action sequences over the state machine | unit, property |
| `ruff check`, `ruff format --check`, `mypy app` (strict), `tsc --noEmit`, production build | clean | – |
| Browser smoke (`npm run e2e`) | 15 checks pass locally and in CI. Includes stopping the MV3 service worker (status verified "stopped") and reading the video context afterwards | real-browser-fixture |
| Remote-code check on the built package | 0 problems; verified to fail on an injected violation | – |
| Secret scan | 0 findings in tracked files, staged changes and full history | – |
| Dependency audit | `pip-audit` (declared backend dependencies) and `npm audit --omit=dev`: no known vulnerabilities | – |
| Docs link check | 0 broken links and anchors | – |
| Clean clone | Fresh venv on Python 3.11 and 3.13 plus `npm ci`: all suites pass | – |
| Live | See below | live |

## Live versus mocked

| Ran live | Mocked only | Never run |
|---|---|---|
| Video claim extraction: 5 runs, earlier sessions | The whole verification pipeline (scripted model) | A complete live fact-check |
| Clip offsets reduce video tokens: 3/3 pairs | Search mode (fake providers, mocked pages) | Live search mode (Brave or SearXNG) |
| Claim analysis: 2026-10-14 and 2026-10-15 | The extension against a fixture YouTube page | The extension on real youtube.com |
| Grounding request reached the provider: 429 quota | – | – |

Artifact of the 2026-10-15 run:
[`eval/factchecks/live-smoke-eiffel-20261015T230152Z.json`](../../eval/factchecks/live-smoke-eiffel-20261015T230152Z.json).

- It made 2 model calls, against a cap of 4, with SDK retries disabled.
- The analysis step took 743 tokens.
- The run then stopped on the quota error, as designed.

## ML evaluation report (real measurements only)

| Item | Value |
|---|---|
| Labelled dataset | None. 0 human-verified records; 1 AI-reviewed reference (`en-debate-1200`), whose scores are provisional |
| Extraction accuracy, claim selection, stance accuracy, verdict accuracy, false-confident rate, citation accuracy | **Not measured.** Blocked on labels and on live verification |
| Extraction latency | Median 30.9 s, range 20.7–88.5 s (n=4, `gemini-3.8-flash`, 55 s window); 9.7 s once on `gemini-3.6-flash` |
| Extraction tokens and cost | Input 3576–3642 tokens; output incl. thinking 735–2623; $0.0054–0.0125 per extraction (n=4) |
| Extraction consistency | The same request returned different claims and claim types. Observed, not quantified |
| Verification latency, tokens, searches, cost | **Not measured.** 0 completed live verifications |
| Analysis step | 743 tokens, single sample (2026-10-15) |

No confidence intervals are given because no metric has a meaningful sample. Every rate above
is either a range over n ≤ 4 or not measured.

## Security assessment

**Fixed in this audit:**

- Prompt-injection escaping (H-2, H-13).
- Streamed body limit (H-4).
- Rate-limiter memory growth (H-5).
- Display-link check for numeric host spellings (H-6).
- Control characters in input (H-7).
- Cost amplification: disconnects (H-9), duplicate requests (H-10), double clicks (H-11).

**Tested and holding:**

- SSRF protection: DNS pinning, per-hop checks, every IPv4 spelling, IPv6-mapped and
  link-local addresses, cloud metadata, redirects to private hosts, decompression bombs and
  redirect loops.
- The extension requests minimal permissions, loads no remote code, and has no XSS sinks.
- Secrets: the scanner runs in CI, history is clean, and logs are redacted.

**CI:** the token is now read-only, superseded runs are cancelled, and dependency audit,
remote-code check and the real-browser smoke test were added.

**Remaining risks:**

1. **The exposed Gemini key is still valid** (L-9). It must be rotated.
2. **The API is anonymous, and all limits are per process.** A public deployment is blocked
   ([DevOps handoff](devops-handoff.md)).
3. **Whether the model obeys injected instructions cannot be tested offline.** Only the
   delimiting is tested.
4. **GitHub Actions are pinned by tag, not commit SHA.** The Node 20 actions produce a
   deprecation warning.
5. **Backend dependencies use version ranges without a lock file.**

## Policy decisions for the owner

1. **Single unverified origin (L-2).** Today a confident verdict may rest on one grounding
   summary or one search snippet, with a warning. A rule that downgrades it to
   `insufficient_evidence` would match the attribution standard: two independent origins for
   unverified text. Prototyped, that rule changed 5 existing outcomes, including the default
   grounding mode's common case. It trades recall for precision. Decide after labelled data
   exists, or accept the conservative rule now.
2. **Semantic attribution check (L-1).** Implemented on 2026-10-19 as a bounded model stage
   (H-25, [`semantic-temporal-report-2026-10-19.md`](semantic-temporal-report-2026-10-19.md));
   its live accuracy still has to be measured on labelled passages.

## Release readiness

| Level | State | Failed or blocked gates |
|---|---|---|
| 1. Engineering correctness | **Mostly met** | FAIL: no backend lock file. FAIL: the exposed key is still valid |
| 2. Functional correctness | **Partly met** | PASS: video context on fixture pages, manual claims, safeguards, error semantics. BLOCKED: real retrieval, real youtube.com |
| 3. Empirical ML validation | **Not met** | BLOCKED: 0 live fact-checks, 0 human labels, no accuracy metrics |
| 4. Closed beta | **Not met** | BLOCKED: no HTTPS deployment, shared limits, monitoring or privacy policy |
| 5. Public launch | **Not met** | Everything in levels 3 and 4, plus store review and abuse protection |

## Next three actions

1. **Rotate the exposed Gemini key.** Then obtain grounding quota (a billed key) or a Brave
   key, and run `scripts/verify_claim.py` on 3–5 claims with a call cap. That produces the
   first complete live fact-check and verification latency and cost figures.
2. **Label 30–50 video moments by hand** (EN and RU): exact words, claim, reference verdict and
   evidence. This unblocks every accuracy metric and the L-2 policy decision.
3. **Run the extension on real youtube.com locally:** `npm run e2e:live` plus the manual
   checklist in [development.md](../development.md#testing-the-full-user-flow), and capture
   the debate evidence fixture.
