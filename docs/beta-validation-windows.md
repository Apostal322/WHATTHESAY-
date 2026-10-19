# First live fact-check: owner procedure (Windows)

The development sandbox cannot do this: it reaches only the Gemini API, and the only key it
has is the one that was exposed in a chat. Run these steps on your own machine. Budget: at most
**6 model calls** and well under **$1** (about $0.03 in tokens per claim plus Google Search
grounding fees for up to 4 queries per claim, depending on your plan).

## 0. Rotate the key (required)

The key pasted into a chat on 2026-10-13 is compromised. In Google AI Studio, delete it and
create a new one. Put the new key only in the repository's `.env`
([safe entry on Windows](windows-local-validation.md)), never in a chat or a file you commit.

## 1. Check what your machine can reach (no calls billed)

```powershell
cd backend; .\.venv\Scripts\Activate.ps1; cd ..
python scripts\diagnose_env.py
```

Note `gemini_key_fingerprint`: it must differ from the old key's. `gemini_api` must be
reachable. Then make the three bounded live checks (1 model call, 1 grounded call, 1 search
call if a search key is set):

```powershell
python scripts\diagnose_env.py --live
```

Stop if `gemini_grounding` says `quota_exhausted`: grounding needs quota on your key (or set
up Brave search, see `.env.example`). `overloaded` means try again later.

## 2. Two manual-claim fact-checks (6 calls)

```powershell
$env:YOUVERIFY_GEMINI_DAILY_CALL_LIMIT = "6"; $env:YOUVERIFY_MAX_RETRIES = "0"
python scripts\verify_claim.py --id en-debate-1200-p2 --language en `
  --claim "In late August 2024 Donald Trump said he would vote against Florida's Amendment 4." `
  --context "Reported 30 August 2024; discussed at the 10 September 2024 ABC presidential debate."
python scripts\verify_claim.py --id en-debate-1200-p5 --language en `
  --claim "Florida's 2024 Amendment 4 would have barred laws restricting abortion before viability." `
  --context "Florida ballot initiative, November 2024."
```

Each run writes `eval/factchecks/<id>-<time>.json` and a `.md` review sheet. This is **live
manual-claim verification**, not video-to-verdict validation.

## 3. Review (the result counts only after this)

Open every cited source in the `.md` sheet and fill in its review section: does each source
say what its excerpt claims, is the speaker right, is the verdict justified at the claim's
date? Compare with `eval/reference/en-debate-1200.md` (AI-reviewed, not ground truth).

## 4. The extension on real YouTube

1. `uvicorn app.main:app --host 127.0.0.1 --port 8000` (in `backend\`), then build and load
   the extension ([README](../README.md#installation)).
2. Open `https://www.youtube.com/watch?v=VgsC_aBquUE&t=1200s`, pause, click **Verify claim**.
3. Check: the right sentence and speaker are proposed; you can edit; Cancel works; the verdict
   shows sources that open; a second video after navigation shows its own title.

That is the first **live video-to-verdict** run. Record what happened (screenshots welcome)
in a new issue or in `eval/factchecks/`.

## 5. Commit the artifacts

```powershell
python scripts\scan_secrets.py --staged
```

Then commit `eval/factchecks/*` and open a pull request.
