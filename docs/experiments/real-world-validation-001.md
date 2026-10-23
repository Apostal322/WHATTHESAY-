# Real-world validation 001 (2026-10-19): BLOCKED BY CREDENTIALS

First attempt at a reproducible live experiment with YouVerify: two propositions of the debate
case `en-debate-1200` checked through the real pipeline (live inference, live retrieval, the
speaker's-words check, deterministic safeguards), then human review.

**Result: not run.** The only Gemini key available to the development environment is the key
exposed in a chat on 2026-10-13. It must not be used, and the application refuses it in code.
No replacement key, no Brave key and no SearXNG instance were configured. The network policy
also blocks search providers, publishers and YouTube. **0 model calls were made; $0 spent.**
Nothing below is, or replaces, a live result. Highest level demonstrated: **E0** (environment
prepared, commands validated).

## A. Environment (genuine observation, no credentials sent)

| Item | Observed |
|---|---|
| OS / runtime | Linux 6.18 (Ubuntu 24.04.5 LTS), Python 3.13.16, Node 22.22.0, Playwright Chromium 1194 |
| Code | branch `feat/functional-beta`, base commit `3c44d42`, plus this round's experiment-tooling changes (committed with this report) |
| Configuration | retrieval `gemini_grounding`; model `gemini-3.8-flash` (configured, not confirmed against the live model list); no fallback model |
| Gemini key | configured, but `gemini_key_is_the_exposed_one: true` (block-listed fingerprint); no replacement |
| Brave / SearXNG | not configured |
| Gemini API endpoint | reachable (unauthenticated probe: HTTP 404, which shows connectivity, not access) |
| Brave API, YouTube, Reuters, Florida DOS, Roll Call, Wikipedia | `blocked_by_proxy` (the sandbox egress proxy refused the connection; not a DNS failure) |
| Test environment | functional (section K) |

Source: `scripts/diagnose_env.py` without `--live`; the private `environment.json` keeps the
same data without the key fingerprint.

## B. Execution

| Step | Command (owner runbook form) | Real or simulated | Attempts | Outcome |
|---|---|---|---|---|
| Diagnostic | `python scripts/diagnose_env.py` | real (unauthenticated) | 1 | as section A |
| Plan P5 | `verify_claim.py --proposition en-debate-1200:P5 … --plan` | real, no calls | 1 | valid; warnings: exposed key, uncommitted tree |
| Plan P2 | `verify_claim.py --proposition en-debate-1200:P2 --statement-date 2024-08-30 … --plan` | real, no calls | 1 | valid; same warnings |
| Run P5 | same command without `--plan` | real | 1 | **refused before any request** (exposed key) |
| Run P2 | — | not attempted | 0 | the same blocker; repeating it would prove nothing |
| Negative control | — | not attempted | 0 | blocked |
| Metrics | `verify_claim.py --summarize` | real, no calls | 1 | 0 runs; ledger 0 calls |

**Call ledger:** 0 of 8 calls used (0 successful, 0 failed, 0 retries, 0 fallbacks, 0 semantic
checks); estimated spend $0 of $1.

## C. Case P2 (not run)

- **Proposition (verbatim, reference record):** "Trump then said he would vote against Florida's
  Amendment 4, the abortion-rights ballot measure." Case context from the record; statement date
  2024-08-30 as reported (event E2, `date_basis: reported`). Reference: `supported` (AI-reviewed,
  provisional).
- Retrieved sources, assessments, verdict, latency, cost: **none (BLOCKED)**. The candidate URLs
  (Reuters, Roll Call Factbase) are reference-record entries located by the user or an AI
  assistant, not anything the application retrieved. No source was supplied to the application.

## D. Case P5 (not run)

- **Proposition (verbatim):** "Amendment 4, if adopted, would have barred laws that prohibit,
  penalize, delay or restrict abortion before viability, so it would have made Florida's six-week
  law unenforceable; a vote against it is a vote to keep that law in force." Note: it is
  compound (legal text, legal effect, and what a vote means). The last part is an inference, not
  a quotation of the initiative. Reference: `supported` (AI-reviewed, provisional; the official
  text, U3, has not been opened by anyone on the project).
- Retrieved sources, assessments, verdict, latency, cost: **none (BLOCKED)**.

## E. Negative control

Not executed (blocked). The mechanics are tested offline: a real quotation by the right speaker
that does not state the proposition backs no verdict (`attribution_not_entailed`). Its
**semantic accuracy is NOT MEASURED**.

## F. ML correctness

No live observations, so no attribution, entailment, citation or verdict quality can be
reported. Offline, the mechanics are covered by 34 regression cases in
`backend/tests/test_semantic_attribution.py`, all with **scripted** judgments, all passing:
support, contradiction, unrelated authentic quotation, negation, numerical mismatch, wrong date,
paraphrase, someone else's words, mixed/compound claims, Russian <-> English, failed or
incomplete checks. They test how the pipeline uses judgments, not whether the model makes them
correctly. No accuracy is computed: no live judgments exist, and the labels are not
human-verified.

Known points a live run must examine (from the code, not from observations):

- In grounding mode the excerpts are Google's summaries, not page text. A claim about what
  someone said then needs the speaker's words from two independent sources before the
  speaker's-words check runs; otherwise the result is `insufficient_evidence`.
- P5's last clause ("a vote against it is a vote to keep that law in force") is an inference
  the official text cannot state verbatim; a strict evaluator may return `mixed`.

## G. Performance

NOT MEASURED (no run). The only earlier live measurements remain those in
`docs/live-validation-report.md` (extraction, n=4).

## H. Human review

Nothing to review from this experiment. Still owed by the owner: the reference record's open
items (open U2, U3, U4/U5; human sign-off), and review of the first live runs using the checklist
in each review sheet.

## I. Application defects found and fixed this round

- **H-34:** the experiment could not be budgeted or kept private as required. The call limit was
  per process, live artifacts went to a tracked directory, and the runbook checked paraphrases.
  Fixed with a persistent call ledger, a private output directory, `--proposition` and
  `--summarize`.
- **H-35:** the Windows setup guide cloned the merged PR #1 branch, which has none of the
  experiment code.
- The runner's `--plan` and summary output is now ASCII-safe for Windows consoles.

Details: `docs/quality/defects.md`.

## J. Level

**E0.** No E1: no real inference completed. No E2–E5.

| Status category | Result |
|---|---|
| Offline browser smoke (fixture page, scripted model) | PASS (15/15) |
| Real YouTube browser interaction | BLOCKED (YouTube unreachable) |
| Real video extraction | BLOCKED |
| Live source-backed verification | BLOCKED BY CREDENTIALS |

## K. Validation of this round

- Backend `pytest`: 458 passed.
- Extension `vitest`: 65 passed.
- Clean: ruff (`backend` and `scripts`), ruff format, mypy (strict), tsc, build, remote-code check.
- Browser smoke: 15/15. Secret scan and doc links: clean.

## L. What the owner needs to do

1. Create a new Gemini key in Google AI Studio and put it in `.env` (never on a command line).
2. Clone `feat/functional-beta` (or `main` once PR #14 is merged), set up as in
   [Windows local validation](../windows-local-validation.md) steps 1–2, run
   `python scripts\diagnose_env.py` and check `gemini_key_is_the_exposed_one: false`.
3. Run [the owner procedure](../beta-validation-windows.md) step 2: P5, then P2, each with
   `--plan` first (`"warnings": []`). Then review the sheets (step 3). That is the first E3 and
   E4 attempt, within 8 calls and about $1.
