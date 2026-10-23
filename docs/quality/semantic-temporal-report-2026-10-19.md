# Semantic correctness and temporal reliability (2026-10-19)

Branch `feat/functional-beta`, from `11d14f5`. Scope: the remaining ways YouVerify could present
a misleading, confident conclusion. Model calls this round: **0**; spend: **$0**. The only
configured key is the exposed one and must not be used; nothing below is live validation.

## Defects

Each was reproduced on the code before its fix, by a test or a probe of the same inputs.

| ID | Sev | Defect | Fix |
|---|---|---|---|
| H-25 (L-1, #6) | S1 | A real quotation by the right speaker on another topic confirmed a claim about what they said; names in another script could never match, so Russian <-> English evidence never counted | Identity, authenticity and entailment checked separately; a bounded entailment stage |
| H-24 | S2 | Russian transcript labels (`ИВАНОВ: ...`) were not recognised as direct speech | Latin and Cyrillic labels |
| H-26 (corrects H-22) | S1 | The upload date was sent as the statement date, and the evaluator was told to judge the claim as of it; evidence had no dates | Five dates kept apart; the upload date is an upper bound only |
| H-27 (extends H-23) | S1 | After navigation, video A's verdict stayed on screen under video B; retry used B's duration | Each check carries its video and is held back on another video until asked |
| H-28 | S2 | SearXNG results with failed upstream engines were cached as complete (search and verdict caches) | Counted as degraded: notice, no caching |
| H-29 | S3 | A fallback model's answer was cached for an hour under the configured model's key | 5-minute TTL for fallback-served results |

Before/after on the same inputs (scripted model, pipeline real):

| Case | Before (`11d14f5`) | After |
|---|---|---|
| Right speaker, another topic (L-1) | `supported` | `insufficient_evidence` (`attribution_not_entailed`) |
| Russian transcript, English claim | `insufficient_evidence` (identity) | `supported` |
| English transcript, Russian claim | `insufficient_evidence` (identity) | `supported` |
| Russian transcript, Russian claim | `insufficient_evidence` (identity) | `supported` |
| Panel: verdict visible after navigating to another video | yes | no (held, shown labelled on request) |
| Panel: retry duration for video A while B (600 s) plays | 600 | 1800 (A's) |

## 1. Semantic attribution (issue #6)

**Evidence to verdict, traced.** Analysis -> retrieval -> evaluation (per-source stances and a
verdict) -> safeguards. For a claim about what someone said, the old safeguard required a source
backing the verdict to contain the speaker's direct speech (a quotation naming them, or a
transcript line under their label), in page text or two independent origins. Nothing checked what
those words said: the evaluator's stance was the only semantic judgement, and it is the step that
fails in L-1.

**Now three checks, all required for a confident verdict:**

1. *Identity* (deterministic): the passage holds the named speaker's words. Across scripts
   (Ivanov / Иванов) spelling cannot decide, so any direct speech is a candidate and the model
   must confirm the speaker.
2. *Authenticity* (deterministic): page text, or two independent origins; exact quotes verbatim,
   except across scripts, where only a faithful translation can be judged (by step 3).
3. *Entailment* (`entailment.py`): one bounded call sees only the claim, the named speaker and up
   to 8 passages of up to 700 characters. It splits the claim into parts and judges each part
   against each passage (entails / contradicts / neutral) and who speaks in each passage. No
   verdict, explanation or other source is shown to it.

**Decision** (`apply_entailment`): a part counts as said or contradicted only through passages by
the named speaker that meet the authenticity rule. All parts said: `supported` stands. A part
contradicted and none said: `contradicted` stands (a conjunction is false if one part is). Some
parts said and the rest not established or contradicted, or the speaker's words going both ways:
`mixed`. The evaluator and this stage disagreeing, or the stage failing: `insufficient_evidence`,
never the opposite verdict, and the result is not cached. Paraphrases and translations entail
when meaning, polarity, numbers, dates, time frame, subject and object are unchanged.

**Backend choice.** Investigated: an offline multilingual NLI model (XNLI-style mDeBERTa-v3 or
XLM-R). Free per call and deterministic, but a large dependency, trained on short sentence pairs
rather than reported speech in transcripts, and it could not be downloaded or evaluated here
(the Hugging Face hub is blocked from this environment). The model stage needs no new
dependency and handles reported speech and Russian <-> English; `EntailmentChecker` is the seam
for adding an NLI backend and comparing both on the same labelled passages.

**Cost and latency (estimates, not measured).** One extra call, only for attribution claims
that reach a confident verdict after the deterministic checks pass. Input about 820 tokens for
the debate case, about 2,300 worst case; at the unconfirmed $0.75 / $3.75 per 1M tokens and
300–1,000 output tokens: about $0.002–0.006 per check. Latency: not measurable without a key.

**Behaviour change to note.** The debate case (en-debate-1200) moves from `supported` to
`mixed` under a strict reading: Trump's own words state two of the claim's three parts; "would
vote to support the six-week ban" is the moderator's characterization of "I will be voting no".
The reference record accepts both verdicts. Transcript excerpts that omit the question being
answered ("I will be voting no") will be judged neutral.

**Status of #6 and the xfail.** The strict xfail is removed: the pipeline now enforces the
missing check, and 24 tests (L-1, paraphrase, negation, number, date, both-ways, compound,
another speaker, snippet-only, RU <-> EN, translated quote, check unavailable, cost bounds,
prompt escaping) pass with scripted judgments. Whether the live model judges correctly is
**not tested**; issue #6 should stay open until it is measured on labelled passages
(quality matrix `attribution.semantic_match.model_accuracy`, BLOCKED).

## 2. Temporal reasoning

| Date | Source | Use |
|---|---|---|
| Video publication | page microdata, only when it names the current video | Upper bound: said on or before |
| Recording / statement | the clip itself (extraction, validated) or the user; partial dates allowed | Judge the claim as of it, only when known |
| Event described | the claim's wording | Evaluator's scope rules |
| Evidence publication | page metadata (meta, JSON-LD, `<time>`), Brave `page_age`, SearXNG `publishedDate` | Shown per source; in the API |
| Today | clock | Bound for all of them |

When the statement date is unknown, both prompts (search planning and evaluation) say so,
including that the video may be archival, a compilation, a rebroadcast or a re-upload, and the
evaluator is told to prefer `mixed` / `insufficient_evidence` over judging against the wrong
period. A statement date after the upload date, or any date in the future, is dropped and
reported. The reconciled context is part of the cache and coalescing key. Tests: 22 in
`tests/test_temporal.py`. Model compliance and date reading from real clips: **not tested**.

## 3. Navigation safety

Each check carries its video (id, title, moment, duration, upload date, clip date) and shows
"Claim from <title> · <time> · Said: <date or unknown>" at every step. While another video
plays, a finished or editable check is held back ("This result is for another video") until the
user asks to see it, then shown labelled; a pending check stays bound and its result is held on
arrival; an extraction answer for a different video is rejected; retries use the check's own
video; the previous finished check can be brought back, still bound (never while another
check runs). Tested (11 cases): SPA
navigation, rapid navigation, tab switching, pending requests, returning to the video, a context
gap (service worker restart), mismatched answers, retry details, recovery. Browser smoke: a new
real-Chromium check that the verdict is held after navigation. Real youtube.com: **not tested**.

## 4. Caching

Checked and pinned by tests: a failed step (search, page fetch, upstream engine, semantic check)
is never cached; zero results without a failure are cached for 5 minutes; a "nothing found"
caused by a failure is not cached; failed retrievals are not cached; fallback answers for 5
minutes; the configured model, policy version, semantic checker, extraction settings and
reconciled dates are part of the key; identical concurrent requests share one run, different
dates do not. Fixed: H-28, H-29.

## Results (this branch)

| Check | Result |
|---|---|
| Backend `pytest` | **433 passed, 0 xfailed** (was 383 passed, 1 xfailed) |
| Extension `vitest` | **65 passed** (was 55) |
| ruff, ruff format, mypy (strict), tsc, build, remote-code check | clean |
| Browser smoke | 15/15 checks |
| Secret scan, doc links | 0 findings; 27 files, 0 broken links |

## Remaining blockers to real-world validation

1. Owner: rotate the exposed key; then `docs/beta-validation-windows.md` steps 1–3 (7 calls,
   plus the optional 4-call entailment control).
2. Owner: label attribution passages (EN, RU, cross-language) and the 6 prepared cases; then
   measure the entailment stage's agreement, cost and latency.
3. Network: YouTube, Brave and publishers are unreachable from this environment.
