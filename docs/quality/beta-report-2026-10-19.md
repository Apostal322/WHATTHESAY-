# Functional-beta round: engineering report (2026-10-19)

Branch `feat/functional-beta` from `main` @ `7f3ac58`.

## A. Repository state

- `main` @ `7f3ac58` (PR #1 merged 2026-10-18); CI green on `main`.
- This work: branch `feat/functional-beta`, pull request opened for review (not merged).

## B. Real-world validation

| Stage | Status | Evidence |
|---|---|---|
| Environment reachability | **Measured**: only `generativelanguage.googleapis.com` is reachable; Brave API, YouTube, Reuters, Florida DOS, Roll Call and Wikipedia are blocked by the sandbox proxy | `scripts/diagnose_env.py` (run without credentials) |
| Credentials | Only the Gemini key exposed in a chat on 2026-10-13 is configured; by policy it must not be used. No Brave key, no SearXNG | `diagnose_env.py` configuration block |
| Live manual-claim verification | **BLOCKED** (no usable key; search providers and publishers unreachable) | – |
| Live video-to-verdict | **BLOCKED** (as above, plus YouTube unreachable) | – |
| Earlier live runs (unchanged) | Extraction: 5 successes; claim analysis: 2; grounding: 429 quota every time | `docs/live-validation-report.md`, `eval/factchecks/` |

Everything else in this round ran offline, with scripted model output and synthetic sources.
Model calls made this round: **0**; spend: **$0**.

## C. First fact-check

Not produced in this environment. The owner procedure
[`docs/beta-validation-windows.md`](../beta-validation-windows.md) runs it on a machine with a
rotated key: two manual claims from the debate case (P2: Trump said he would vote against
Amendment 4; P5: the amendment's legal effect), at most 6 calls, then a human review of every
cited source, then the extension on the real video.

## D. Bugs fixed

| ID | Defect | Fix | Regression test |
|---|---|---|---|
| H-18 (S1) | "supported" while the model's own evidence-backed finding contradicted part of the claim (unsupported cause, misleading context) | Safeguard `verdict_contradicts_own_findings` → `mixed` | `test_scenarios.py` (failed before) |
| H-19 (S1) | Evaluation computed "accuracy" from unreviewed labels | Review status on items; accuracy only over human-verified; annotation cases separate machine proposals from human labels | `test_eval_metrics.py`, `test_annotation.py` |
| UX | Errors showed provider jargon ("The Gemini API quota for this key has been exceeded") | Plain headline ("Evidence search is temporarily unavailable. Your claim is saved."), details collapsed | `ErrorPanel.test.tsx`, `App.test.tsx` |

New scenario suite (`tests/test_scenarios.py`, 13 cases through the real pipeline): false
speaker attribution, fabricated quotation, correct quote in misleading context, syndicated
reports, unsupported causal inference, partial truth, wrong statistic, headline-only mention,
conflicting primary sources, irrelevant results, missing evidence, failed search, opinion.

## E. Expected failure (L-1)

`test_l1_speakers_own_words_on_another_topic_should_not_confirm` (strict xfail, severity S1).
Reproduced: a transcript line by the right speaker on another topic, wrongly assessed as
supporting by the model, passes the attribution safeguard. Root cause: the safeguard checks
that the speaker's own words exist, not that they entail the claim. Not fixable safely here:
word overlap would reject paraphrases and every cross-language case, and a semantic check (NLI
or a bounded model call) cannot be validated without a usable key. Kept as strict xfail; work
tracked in issue #6.

## F. Test results (this branch)

Measured on this branch: backend 376 passed,
1 xfailed; extension 50 passed; ruff, mypy (strict), tsc, build, remote-code check, secret
scan and link check clean; browser smoke 14/14 checks passed.

## G. ML evaluation

No accuracy measured. Prepared: 6 real moments (2 RU, 4 EN) in
`eval/annotation/cases.jsonl`, 4 with live machine proposals attached, 0 human-verified.
Guide: [`docs/annotation-guide.md`](../annotation-guide.md).

## H. Performance

No new measurements (no live calls possible). Existing: extraction median 30.9 s, range
20.7–88.5 s, $0.0054–0.0125 per extraction (n=4). Verification latency and cost: not measured.

## I. Security

Unchanged guarantees, re-verified by the suite (SSRF, injection escaping, body limits,
cancellation, secrets). New: the diagnostic never prints a key, only a 12-character SHA-256
fingerprint, and sends no credentials without `--live`. Remaining: the exposed key must be
rotated; per-process limits (DevOps issues #9–#13).

## J. Readiness

| Level | State |
|---|---|
| Engineering-ready | Yes: tests, CI, safeguards, security controls |
| Functional prototype (live) | **Not demonstrated**: no live fact-check yet |
| Human-validated prototype | No: 0 human-verified labels |
| Closed beta / public | No |

## K. Remaining blockers

| Kind | Blocker |
|---|---|
| Credentials | Exposed key not rotated; no Brave key |
| Provider | Google Search grounding quota (429) on the previous key |
| Network | Sandbox blocks YouTube, publishers and Brave; only Gemini reachable |
| Human review | No labelled cases; no reviewed live result |
| Application | L-1 (semantic attribution), extraction latency and consistency |

## L. Next actions

1. Owner: rotate the key and run `docs/beta-validation-windows.md` steps 1–3 (≤ 6 calls).
2. Owner: annotate the 6 prepared cases, then grow to 30–50 (`docs/annotation-guide.md`).
3. Engineering: semantic attribution check (issue #6), validated on the annotated cases.

## M. Hardening round 2 (same day)

Four defects found by audit, each reproduced by a test that failed before its fix
(register: [`defects.md`](defects.md) H-20 to H-23):

| ID | Defect | Status |
|---|---|---|
| H-20 (S2) | Verdicts from a partly failed search, or with no evidence, were cached for an hour | Fixed |
| H-21 (S3) | Extraction cache ignored clip window, fps, resolution and prompt version | Fixed |
| H-22 (S2) | Evaluator never knew when the claim was made | Mitigated: date sent and used; real YouTube markup and model compliance not tested |
| H-23 (S2) | A claim from a previous video was shown under the new video with no notice | Fixed |

`VERIFICATION_POLICY_VERSION` bumped to `2026-10-19.2` (evaluation prompt changed). Model
calls this round: 0. Results after this round: backend 383 passed, 1 xfailed (L-1); extension
55 passed; browser smoke 14/14 checks passed.
