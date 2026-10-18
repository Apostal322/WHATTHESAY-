# Local validation on Windows

Exact PowerShell steps to run YouVerify on your own PC, in an order that spends **no API credit until
the last steps**, and to find out whether your network removes the limits of the cloud development
environment. That environment's network policy blocks every publisher site, YouTube and Brave; only the
Gemini API host was reachable (2026-10-14, `scripts/capture_evidence.py --check-only`). Whether your
network does better is **not assumed**: step 3 measures it.

Requirements: Windows 10/11, Python 3.11+ (`py --version`), Node.js 22.12+ (`node --version`),
Git, Google Chrome.

## 1. Clone the development branch

```powershell
git clone --branch claude/kind-hawking-oybsqt https://github.com/Apostal322/WHATTHESAY-.git youverify
cd youverify
git log --oneline -3        # the newest commit is the one you expect
```

## 2. Python environment and tests (no key, no network calls)

```powershell
cd backend
py -3.11 -m venv .venv                  # or: python -m venv .venv
.\.venv\Scripts\Activate.ps1            # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install --upgrade pip
pip install -e ".[dev]"
pytest -q                               # expect: all passed (no key, no network)
ruff check . ..\scripts; mypy app
cd ..
```

## 3. Does your network reach what the cloud could not? (no API calls)

```powershell
backend\.venv\Scripts\python scripts\capture_evidence.py --check-only
```

It prints DNS and HTTP status for each evidence source, YouTube, the Gemini API host and Brave
Search, sending no key. Read it like this:

| Column result | Meaning |
|---|---|
| `dns_ok` + `http_200`/`http_3xx` | reachable |
| `dns_ok` + `http_401`/`http_403`/`http_404` | reachable; the site refused a bare request (normal for API roots; a 403 from a news site means it blocks bots: use `--html-file` below) |
| `dns_failed` / `http_failed` | blocked or offline on this network |

Record the output in your notes. Only rows that come back reachable are evidence that local access
resolves a cloud limitation.

## 4. Secrets, safely

Prefer a **session-only** variable, which is never written to disk and disappears when the window
closes:

```powershell
# Works in Windows PowerShell 5.1 and PowerShell 7: the key is typed masked and never echoed.
$sec = Read-Host "Gemini API key" -AsSecureString
$env:GEMINI_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
Remove-Variable sec
$env:YOUVERIFY_GEMINI_DAILY_CALL_LIMIT = "20"                 # hard cap per backend process
$env:YOUVERIFY_MAX_RETRIES = "0"                              # one request per call, no silent retries
```

Do **not** type `$env:GEMINI_API_KEY = "..."` with the key in it: PowerShell's command history
(PSReadLine) saves every typed line in plain text to
`%APPDATA%\Microsoft\Windows\PowerShell\PSReadLine\ConsoleHost_history.txt`. If that has
happened, delete the line from that file and rotate the key.

Or keep it in `.env` (git-ignored: check with `git check-ignore -v .env`, which must print a match):

```powershell
Copy-Item .env.example .env
notepad .env        # GEMINI_API_KEY=..., YOUVERIFY_GEMINI_DAILY_CALL_LIMIT=20, YOUVERIFY_MAX_RETRIES=0
```

Never paste the key into the extension, a commit, an issue or a chat. Before committing anything:
`git status` must not list `.env`, and the secret scan must report 0 findings (it recognises both
`AIza…` and `AQ.…` Google key formats and never prints a key):

```powershell
backend\.venv\Scripts\python scripts\scan_secrets.py --staged
```

Check which models the key can use (1 list request, not a generation):

```powershell
backend\.venv\Scripts\python scripts\list_models.py
```

## 5. Capture the evidence fixture (no API calls)

Only if step 3 showed the sources reachable:

```powershell
backend\.venv\Scripts\python scripts\capture_evidence.py
git diff -- eval\fixtures\en-debate-1200\fixture.json      # read every captured excerpt
```

For a site that refuses the app's fetcher (403, cookie wall): open it in Chrome, press Ctrl+S, choose
"Webpage, HTML only", then:

```powershell
backend\.venv\Scripts\python scripts\capture_evidence.py --source U2 --html-file "$HOME\Downloads\u2.html"
```

Then run the tests again (`cd backend; pytest -q tests\test_fixture_en_debate_1200.py`): every
captured excerpt is checked against the excerpt policy.

## 6. Run FastAPI

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Check <http://127.0.0.1:8000/health>: `"gemini_configured": true` means the key loaded. API docs:
<http://127.0.0.1:8000/docs>.

## 7. Build and load the Chrome extension

Second PowerShell window:

```powershell
cd youverify\extension
npm ci
npm run typecheck; npm test
npm run build                         # produces extension\dist
```

In Chrome: `chrome://extensions` → Developer mode → **Load unpacked** → select `extension\dist` →
pin YouVerify AI. Copy its ID and restrict the backend to it (`.env` or session variable), then restart
uvicorn:

```powershell
$env:YOUVERIFY_ALLOWED_ORIGIN_REGEX = "^chrome-extension://<your-extension-id>$"
```

## 8. Manual claim verification (3 model calls, or 2 + search fees)

From the API, without the extension:

```powershell
$body = @{ claim = "The Eiffel Tower was completed in 1889."; language = "en" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/v1/claims/verify `
  -ContentType "application/json" -Body $body | ConvertTo-Json -Depth 6
```

Or with a review artifact (writes `eval\factchecks\<id>-<time>.json/.md`):

```powershell
backend\.venv\Scripts\python scripts\verify_claim.py --claim "The Eiffel Tower was completed in 1889." --id eiffel
```

In the extension: open the side panel on any page → **Type a claim manually**.

If Gemini grounding returns `provider_quota_exhausted` (429) on a free-tier key, the alternative is the
search backend, which needs no grounding quota: `YOUVERIFY_RETRIEVAL_BACKEND=search` with a Brave key
(set `YOUVERIFY_PRICE_PER_1K_BRAVE` and `YOUVERIFY_SEARCH_DAILY_BUDGET_USD`; see
[retrieval architecture](retrieval-architecture.md)).

## 9. The debate case, proposition by proposition (10 model calls)

After step 5 captured at least one source:

```powershell
backend\.venv\Scripts\python scripts\run_fixture_case.py                          # dry run: plan, 0 calls
backend\.venv\Scripts\python scripts\run_fixture_case.py --llm gemini --max-calls 10
```

This is **live evaluation of offline evidence**: the real model judges each proposition (P1 position
on the limit, P2 stated vote, P3 chronology, P4 characterization, P5 legal effect) over the captured
excerpts. It is not a live fact-check, and its scores are provisional (AI-reviewed reference). Start
with `--only P2 --max-calls 2` if Gemini is returning 503s.

## 10. The YouTube timestamp flow (1 extraction call + verification)

1. Open <https://www.youtube.com/watch?v=VgsC_aBquUE> in the Chrome profile with the extension.
2. Seek to about 20:00 (the reference case; the Factbase transcript's 15:55–16:12 is a different
   timeline), let the moderator's question play, then click **Verify claim at mm:ss**.
3. Compare the proposed claim with the moderator's words; edit if needed; click **Check this claim**.

Scripted alternative with a separate Chromium (branded Chrome 137+ ignores `--load-extension`, so use
Chrome for Testing):

```powershell
cd extension
npx @puppeteer/browsers install chrome@stable          # prints the chrome.exe path
$env:CHROMIUM_PATH = "<path printed above>"
npm run e2e:live -- --url "https://www.youtube.com/watch?v=VgsC_aBquUE" --at 1200 --headed
```

Without a key, the mocked flow still runs end to end: `cd backend; python -m tests.e2e_server`, then
`cd extension; npm run e2e` (canned data, clearly labelled).

## 11. Reviewing source pages

For every verdict: open each source link, find the excerpt on the page, and check (a) that the page
says it, (b) the date, (c) whether quoted words are the person's words or the outlet's. The response's
`guardrail_notes` flag quoted words that were not found verbatim in page text, and evidence that came
from an offline fixture. Fill in the "Review (by hand)" section of the artifact's `.md` sheet. To promote
the reference record, a person sets `reviewer_kind: human`, `status: human_verified` and
`human_approved` on the sources they checked in `eval/reference/en-debate-1200.json`.

## What to report back

The `--check-only` table (step 3), whether any fixture capture succeeded (step 5), and the artifact
paths from steps 8–10. Do not paste keys or `.env` contents.
