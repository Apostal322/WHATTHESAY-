# First live fact-check: owner procedure (Windows)

The milestone: **one genuine, source-backed fact-check through the real application pipeline,
then a human review of it.** The development sandbox cannot do this: it reaches only the Gemini
API, and the only key it has is the one exposed in a chat (the application refuses that key).
Run these steps on your own machine.

Budget: at most **7 model calls** for step 2 (11 with the optional control) and well under
**$1**: about $0.01–0.03 in tokens per claim at the assumed prices below, plus Google Search
grounding fees for up to 4 queries per claim, depending on your plan.

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

## 2. The debate case: two fact-checks (up to 7 calls)

Case `en-debate-1200` (ABC presidential debate, 10 September 2024). Its reference record
[`eval/reference/en-debate-1200.md`](../eval/reference/en-debate-1200.md) splits the claim into
propositions; two are checked here, each scored against its own proposition. The reference is
**AI-reviewed, not ground truth**: scores are provisional until a human verifies it.

Set the limits for this PowerShell session. The call limit applies **per run** (each command
is its own process), so 4 bounds every run exactly; retries off; no fallback model:

```powershell
$env:YOUVERIFY_GEMINI_DAILY_CALL_LIMIT = "4"
$env:YOUVERIFY_MAX_RETRIES = "0"
Remove-Item Env:YOUVERIFY_FALLBACK_MODEL -ErrorAction SilentlyContinue   # and not set in .env
$prices = @("--price-in", "0.75", "--price-out", "3.75", "--price-search-1k", "14")   # assumed, unconfirmed
```

**2a. What Trump said (P2).** A claim about what someone said: analysis, grounded retrieval,
evaluation, and, when it reaches a confident verdict, the speaker's-words check (3 or 4 calls).

```powershell
$p2 = @("--id", "en-debate-1200-p2", "--language", "en", "--reference", "en-debate-1200:P2",
  "--statement-date", "2024-08",
  "--claim", "In late August 2024 Donald Trump said he would vote against Florida's Amendment 4.",
  "--context", "Reported 30 August 2024; discussed at the 10 September 2024 ABC presidential debate.")
python scripts\verify_claim.py @p2 @prices --plan     # no calls: check the plan first
python scripts\verify_claim.py @p2 @prices
```

**2b. What the amendment would do (P5).** A factual claim (3 calls).

```powershell
$p5 = @("--id", "en-debate-1200-p5", "--language", "en", "--reference", "en-debate-1200:P5",
  "--claim", "Florida's 2024 Amendment 4 would have barred laws restricting abortion before viability.",
  "--context", "Florida ballot initiative, November 2024.")
python scripts\verify_claim.py @p5 @prices --plan
python scripts\verify_claim.py @p5 @prices
```

`--plan` must show `"warnings": []` before you run; it prints the claim, dates, model, limits,
the commit and whether it is clean. Each run writes `eval/factchecks/<id>-<time>.json` and a
`.md` review sheet. This is **live manual-claim verification**, not video-to-verdict validation.

**Optional control for the speaker's-words check (issue #6; 3–4 calls).** His real words ("I
will be voting no") do not say this.

```powershell
python scripts\verify_claim.py --id en-debate-1200-l1-control --language en --statement-date 2024-08 `
  --claim "In late August 2024 Donald Trump said he would vote to keep Florida's six-week abortion ban." `
  --context "Reported 30 August 2024; discussed at the 10 September 2024 ABC presidential debate." @prices
```

### What to expect

| Run | Acceptable (reference, provisional) | A warning sign |
|---|---|---|
| P2 | `supported` | `contradicted`; `supported` resting on a report that only paraphrases him |
| P5 | `supported` | `contradicted`; a verdict citing commentary instead of the amendment text or official records |
| Control | `insufficient_evidence` or `mixed`, with rule `attribution_not_entailed` or `attribution_without_direct_evidence` | `supported` (the issue #6 failure) |

`insufficient_evidence` on P2 or P5 is not a false verdict, but it is not the milestone either:
look at why (the sheet's safeguards and model-call table) before retrying, and count retries
against the budget.

### What each artifact preserves

- **Input:** claim, context, dates, reference proposition.
- **Run:** UTC time, git commit and whether the tree was clean, policy and prompt versions,
  model, retrieval mode, limits and retries.
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

## 5. Commit the artifacts

```powershell
git add eval/factchecks
python scripts\scan_secrets.py --staged
```

The scan must report 0 findings. Then commit and open a pull request. The artifacts contain
the prompts and the retrieved public web excerpts, never the key.
