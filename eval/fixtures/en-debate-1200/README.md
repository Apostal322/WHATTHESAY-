# Offline evidence fixture: en-debate-1200

**An offline regression fixture, not live retrieval.** It lets the verification pipeline
(analysis → evaluation → guardrails) run on a fixed, inspectable evidence set for the ABC debate
claim, so changes to prompts, guardrails or models can be compared on the same input. A good result
on it shows how the system *reasons over* these sources, not that it can *find* them.

## Status (2026-10-14)

All five sources are **`pending_capture`**: their URLs and metadata are recorded, but **no excerpt
has been captured**. Every source host is denied by the development environment's network policy
(attempts logged in `fixture.json`). No text was typed in, recalled from memory or copied from search
summaries in its place. Until a capture exists, `FixtureRetriever` refuses to run, and
`scripts/run_fixture_case.py` refuses to spend model calls.

| id | source | type | relevant to |
|---|---|---|---|
| U5 | Roll Call Factbase debate transcript (10 Sep 2024) | primary transcript | P1–P4 (quote, speaker) |
| U3 | Florida Department of State, Amendment 4 record | official record | P5, P4 |
| U2 | Reuters, 30 Aug 2024 | wire report | P2, P3, P1 |
| U4 | Roll Call Factbase, Fox interview (30 Aug 2024) | primary transcript | P2, P4 |
| U1 | ABC News debate transcript | primary transcript | P1–P4 |

## Capturing (on a machine with web access)

```powershell
python scripts\capture_evidence.py --check-only     # reachability only; writes nothing, no API calls
python scripts\capture_evidence.py                  # the app fetches each pending source
# a site that blocks the fetcher: save the page in Chrome (Ctrl+S, "Webpage, HTML only"), then
python scripts\capture_evidence.py --source U2 --html-file "$HOME\Downloads\u2.html"
git diff -- eval\fixtures                           # review before committing
```

## Excerpt policy

- At most 3 excerpts per source, each at most 400 characters, cut verbatim by the application's page
  parser around the anchor phrases. Anchors that are not on the page are listed as missing.
- Full pages are not stored. `page_sha256` is the hash of the extracted page text, to detect a changed
  page on recapture.
- `method` says how the page was obtained: `app_fetch_page` (the app's SSRF-guarded fetcher) or
  `user_saved_html` (a person saved it in a browser; the app parsed the file).
- Short excerpts are retained for regression testing, attribution and review. Keep them short; do not
  widen the policy to store articles.
- Capturing a source sets nothing in the reference record. Marking it `inspected_by_app` there, and
  recording stances, are separate review steps; `human_approved` needs a person.
