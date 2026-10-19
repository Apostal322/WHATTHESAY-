# First live fact-check: owner procedure (Windows)

The milestone: **one genuine, source-backed fact-check through the real application pipeline,
then a human review of it.** The development sandbox cannot do this: it reaches only the Gemini
API, and the only key it has is the one exposed in a chat (the application refuses that key).
Run these steps on your own machine.

Budget: at most **8 model calls and about $1** for the whole experiment, enforced by a call
ledger across runs (step 2). Prices used for the estimate: $0.75 / $3.75 per 1M tokens and $14 per
1,000 grounding queries (assumed, unconfirmed; check your plan).

## 0. Rotate the key (required)

The key pasted into a chat on 2026-10-13 is compromised. In Google AI Studio, delete it and
create a new one. Put the new key only in the repository's `.env`
([safe entry on Windows](windows-local-validation.md)), never in a chat or a file you commit.

The application refuses the old key in code (its fingerprint `0ff24a559e0c` is on a block list):
the backend answers `backend_misconfigured` and the scripts stop before any request.

## 1. Check what your machine can reach (no calls billed)

Use the commit under test: a clean checkout of the PR branch, or `main` once it is merged.

```powershell
git status                      # must show no modified files: the run records the commit
cd backend; .\.venv\Scripts\Activate.ps1; cd ..
python scripts\diagnose_env.py
```

Check: `gemini_key_is_the_exposed_one` is `false`; `gemini_api` is reachable. Then the three
bounded live checks (1 model call, 1 grounded call, 1 search call if a search key is set):

```powershell
python scripts\diagnose_env.py --live
```

Stop if `gemini_grounding` says `quota_exhausted`: grounding needs quota on your key (or set
up Brave search, see `.env.example`). `overloaded` means try again later.

## 2. The debate case: two fact-checks (at most 8 calls, $1)

Case `en-debate-1200` (ABC presidential debate, 10 September 2024). Each run checks one
proposition of its reference record [`eval/reference/en-debate-1200.md`](../eval/reference/en-debate-1200.md)
**word for word** (`--proposition`) and is scored against it. The reference is **AI-reviewed,
not ground truth**: scores are provisional until a human verifies it.

**Budget.** A ledger in the experiment directory (`call-ledger.json`) records every model call
*before* it is sent, across runs and restarts: at most **8 calls and about $1** for the whole
experiment (`--budget-calls`, `--budget-usd`), at most 4 per run. A run does not start without
room for all its calls, and failed calls count. Retries and a fallback model would be requests
the ledger cannot see, so a run refuses to start with either.

```powershell
$env:YOUVERIFY_MAX_RETRIES = "0"
Remove-Item Env:YOUVERIFY_FALLBACK_MODEL -ErrorAction SilentlyContinue   # and not set in .env
$exp = "eval\runs\real-world-validation-001"    # git-ignored: full prompts and raw outputs stay local
$common = @("--language", "en", "--out-dir", $exp,
  "--price-in", "0.75", "--price-out", "3.75", "--price-search-1k", "14")   # assumed, unconfirmed prices
```

**2a. P5, what Amendment 4 would have done** (a factual claim; 3 calls). First, because it is the
simpler case:

```powershell
python scripts\verify_claim.py --proposition en-debate-1200:P5 @common --plan > "$exp\experiment-plan-p5.json"
Get-Content "$exp\experiment-plan-p5.json"     # "warnings": [] before you go on
python scripts\verify_claim.py --proposition en-debate-1200:P5 @common
```

**2b. P2, what Trump said** (about what someone said: 3 calls, or 4 with the speaker's-words
check). The statement date is the one the reports give (event E2 in the reference record):

```powershell
python scripts\verify_claim.py --proposition en-debate-1200:P2 --statement-date 2024-08-30 @common --plan > "$exp\experiment-plan-p2.json"
Get-Content "$exp\experiment-plan-p2.json"
python scripts\verify_claim.py --proposition en-debate-1200:P2 --statement-date 2024-08-30 @common
```

**2c. Metrics** (no calls): `python scripts\verify_claim.py --summarize --out-dir $exp` writes
`metrics.json` (per run: outcome, safeguards, calls, tokens, per-call latency, estimated cost;
the ledger totals).

The plan must show `"warnings": []`: no exposed key, a clean commit, retries 0, no fallback,
prices given, enough budget left. Each run writes `<id>-<time>.json` (everything, private) and a
`.md` review sheet into `$exp`. This is **live manual-claim verification**, not video-to-verdict.

**Optional control for the speaker's-words check (issue #6; 3–4 calls).** Only with budget left
after 2a and 2b (`--plan` says how much); otherwise it needs a separately approved budget. His
real words ("I will be voting no") do not say this.

```powershell
python scripts\verify_claim.py --id en-debate-1200-l1-control --statement-date 2024-08-30 @common `
  --claim "In late August 2024 Donald Trump said he would vote to keep Florida's six-week abortion ban." `
  --context "Reported 30 August 2024; discussed at the 10 September 2024 ABC presidential debate."
```

### What to expect

| Run | Acceptable (reference, provisional) | A warning sign |
|---|---|---|
| P5 | `supported` | `contradicted`; a verdict citing commentary instead of the amendment text or official records |
| P2 | `supported` | `contradicted`; `supported` resting on a report that only paraphrases him |
| Control | `insufficient_evidence` or `mixed`, with rule `attribution_not_entailed` or `attribution_without_direct_evidence` | `supported` (the issue #6 failure) |

`insufficient_evidence` is not a false verdict: a complete run that abstains shows the pipeline
works, not that it can produce a source-backed verdict. Look at why (safeguards and model-call
table in the sheet) before spending more budget. In the default grounding mode the excerpts are
Google's summaries, not page text: the sources must be opened by the reviewer (step 3).

### What each artifact preserves

- **Input:** the proposition (word for word, with its reference id), context, dates.
- **Run:** UTC time, git commit and whether the tree was clean, policy and prompt versions,
  model, retrieval mode, limits, and the experiment budget after the run.
- **Every model call, exactly as made:** operation, requested and serving model, full prompt
  (with the excerpts the model saw), system-prompt hash, raw output, grounding queries, sources
  and supports, token usage, latency, and the error code if it failed.
- **The result:** verdict, explanation, findings, safeguards that changed the verdict, warnings,
  the speaker's-words check (passages, claim parts, every judgment), and per-source URL, domain,
  excerpt origin, publication date and retrieval time.
- **Failure:** a failed run keeps what completed before the failure, and its error.

The scripts never write an artifact that contains the API key.

## 3. Review (the result counts only after this)

Open the `.md` sheet and work through its **Review checklist**:
- Open every source and confirm each excerpt is on the page.
- Confirm the speaker's own words are what the verdict relies on.
- Mark your agreement with each speaker's-words judgment.
- Judge the verdict at the claim's date.

Then fill in the review record in the `.json` (`review.status`: `human_verified` only if every
item passed, plus `reviewer` and `notes`).

**The milestone is complete** when at least one of P2 / P5 has a verdict other than
`insufficient_evidence`, every source cited for it was opened and checked, and its review record
is filled in, whatever the review concludes. A reviewed disagreement is a valid result.

## 4. The extension on real YouTube

1. `uvicorn app.main:app --host 127.0.0.1 --port 8000` (in `backend\`), then build and load
   the extension ([README](../README.md#installation)).
2. Open `https://www.youtube.com/watch?v=VgsC_aBquUE&t=1200s`, pause, click **Verify claim**.
3. Check: the right sentence and speaker are proposed; "Claim from" names this video and says
   when it was said (or that the date is unknown); you can edit; Cancel works; the verdict shows
   sources that open; after navigating to a second video the verdict is held back as "for
   another video" and comes back, labelled, on request.

That is the first **live video-to-verdict** run. Record what happened (screenshots welcome)
in a new issue or in `eval/factchecks/`.

## 5. Keep the artifacts private; share the reviewed sheets

Everything in `eval\runs\` is git-ignored: the full prompts, raw outputs and the ledger stay on
your machine. To share the result, copy the **reviewed** `.md` sheets (and `metrics.json`) to
`eval/factchecks/`, then:

```powershell
git add eval/factchecks
python scripts\scan_secrets.py --staged
```

The scan must report 0 findings. Then commit and open a pull request.
