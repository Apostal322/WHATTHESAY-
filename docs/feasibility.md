# Phase 1 — Feasibility: timestamp-based claim extraction with Gemini

**Question:** can Gemini reliably identify a spoken claim around a specific
timestamp in a public YouTube video, without reprocessing the entire video on
every click?

**Status: the integration is implemented and its wire format is verified
offline. Accuracy, latency and cost against live videos have NOT been measured yet**,
because no Gemini API key and no YouTube access were available in the
development environment. Everything below is either verified (and how) or
explicitly marked as an assumption to measure.

## What was verified, and how

| Item | How it was verified |
|---|---|
| google-genai **2.29.0** exposes `FileData(file_uri)`, `VideoMetadata(start_offset, end_offset, fps)`, `GenerateContentConfig(media_resolution, response_schema, …)`, `Tool(google_search=GoogleSearch())`, `GroundingMetadata.grounding_chunks / grounding_supports / web_search_queries` | Read from the installed SDK source (`google/genai/types.py`). |
| The exact HTTP request the backend sends to `…/v1beta/models/{model}:generateContent` | Captured with an `httpx.MockTransport`; pinned by `backend/tests/test_gemini_adapter.py`. The video part serializes as `fileData.file_uri` = canonical watch URL and `videoMetadata` = `{start_offset: "710s", end_offset: "765s", fps: 0.5}`; the response schema is sent as an enum-constrained `responseSchema`. |
| Parsing of grounding metadata and per-modality token usage (VIDEO / AUDIO) | Same mocked-transport tests, using real SDK response types. |
| Provider error classification (429, 404 model, invalid key, inaccessible video, 5xx, timeouts) | Mocked-transport tests. The *exact* error text Gemini returns for private/unavailable videos is an assumption (see below). |

The official docs site (`ai.google.dev`) was blocked by the sandbox network
policy, so documented behaviour was cross-checked through search results that quote it:
public YouTube URLs are passed as `file_data.file_uri`; only **public** videos
are supported (not private or unlisted); the free tier is limited to **8 hours of
YouTube video per day**; Gemini 2.5+ accepts up to 10 videos per request.

## Design decisions that follow from the risk

1. **Clip, don't re-process.** Each extraction sends only a window around the
   click: by default 45 s before to 10 s after (`YOUVERIFY_WINDOW_*`). Viewers press
   the button *after* hearing a statement, so the window is asymmetric.
2. **Low sampling for speech.** Claims are spoken, so frames matter little:
   defaults are `fps=0.5` and `media_resolution=low`. Audio is still processed
   in full for the clip.
3. **Mandatory confirmation.** The extracted claim is always editable before
   verification (`requires_user_confirmation: true`), because timestamp
   alignment is not guaranteed and the model may pick a neighbouring sentence.
4. **Quote vs. paraphrase.** The model must set `is_exact_quote` and is told to
   set it to `false` whenever it paraphrases, translates, or is unsure. The backend
   forces it to `false` if no quote text is returned. The UI labels a quote as
   "AI transcription (may contain errors)" and anything else as "Paraphrase".
   This is still a *model self-report*. Without an aligned transcript it cannot
   be independently verified, and the docs say so.
5. **Manual fallback.** If the video is inaccessible to Gemini (private, region-locked,
   age-restricted, too long) or no claim is found, the UI goes straight to manual entry. The rest of
   the product works on a typed claim with no video at all.
6. **No scraping.** No audio download, no transcript scraping, no DRM
   circumvention. The backend never fetches YouTube itself; it rebuilds a
   canonical watch URL from a validated 11-character ID and passes it to Gemini.

## Open questions, and how to measure them

The PoC script measures all of them with a key:

```bash
cd backend && source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
cd ..
python scripts/poc_extract_claim.py --url "https://www.youtube.com/watch?v=VIDEO_ID" --timestamp 755
python scripts/poc_extract_claim.py --url "https://www.youtube.com/watch?v=VIDEO_ID" --timestamp 755 --language ru
python scripts/poc_extract_claim.py --url "https://www.youtube.com/watch?v=VIDEO_ID" --timestamp 755 --probe-clipping
```

| Open question | How to measure it |
|---|---|
| **Are `start_offset`/`end_offset` honoured for YouTube URLs?** If not, every click bills the whole video. | `--probe-clipping` sends a 20 s and a 50 s window and compares `prompt_tokens`. Proportional growth means clipping is honoured; flat counts mean it is ignored or capped. |
| Does the model select the sentence closest to the click? | Run the eval protocol (docs/evaluation.md) on labelled videos and measure claim F1 plus manual review. |
| How reliable is `is_exact_quote`? | On labelled items, compare `evidence_from_video` against a human transcript of the window. |
| Russian vs English quality | Separate eval slices by `language`. |
| Latency | `diagnostics.latency_ms` in the PoC output; p50/p90 in the eval runner. |
| Exact error codes and messages for private, unlisted or region-locked videos | Run the PoC on such videos; adjust `_classify_api_error` in `backend/app/services/llm.py` if the wording differs. |

## Approximate cost model (to be replaced by measurements)

Costs are **not hardcoded** because prices change. The backend reports actual
token usage, broken down into video and audio tokens, from `usage_metadata` on every
response. If you set `YOUVERIFY_PRICE_INPUT_PER_M` / `YOUVERIFY_PRICE_OUTPUT_PER_M`
from the current [pricing page](https://ai.google.dev/gemini-api/docs/pricing),
each response also includes `estimated_cost_usd`.

Rough expectation for one extraction, using previously published per-frame and
per-second token rates. Treat these as an order of magnitude only and confirm
with the PoC:

- 55 s clip × 0.5 fps ≈ 28 frames at low resolution: on the order of 2k tokens
- audio: on the order of 30 tokens/s, so ≈ 1.7k tokens
- prompt and output: under 1k tokens
- **≈ 4–5k input tokens per extraction**, if clipping is honoured. If clipping is
  ignored, multiply by (video length / 55 s).

A verification makes three model calls (claim analysis, grounded search, evaluation),
typically a few thousand tokens in total. **Google Search grounding is billed
separately** under its own pricing; check the current rate for your model.

## Known limitations

- No guaranteed word-level transcription or timestamp alignment.
- Public videos only; Shorts and live streams are excluded from the MVP.
- Free tier: 8 h of YouTube video per day.
- Model IDs change quickly. The default (`gemini-3.8-flash`) came from search
  results showing it as the current GA Flash model, and could not be verified
  against the live API. Run `python scripts/list_models.py` and set
  `YOUVERIFY_*_MODEL` accordingly.
