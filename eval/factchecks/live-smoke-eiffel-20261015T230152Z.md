# Fact-check review sheet: live-smoke-eiffel

Everything below is **model output or retrieved material** unless a review field says otherwise. Fill in the review section by hand.

## Input
- Input method: **manual**
- Claim checked: The Eiffel Tower was completed in 1889.
- Context (model-written): None

## Result
**No verdict: provider_quota_exhausted** (The Gemini API quota for this key has been exceeded (for example a free-tier daily or search-grounding limit). Try again later, or use a key with a higher quota.)

Diagnostics: `{"operation": "grounded_search", "model": "gemini-3.8-flash", "http_status": 429, "provider_status": "RESOURCE_EXHAUSTED", "provider_message": "You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. "}`

Completed before the failure (model output, unreviewed):
```json
{
  "analysis": {
    "normalized_claim": "The Eiffel Tower was completed in 1889.",
    "claim_type": "factual",
    "planned_queries": [
      "Eiffel Tower completion date 1889",
      "when was the Eiffel Tower built and completed"
    ],
    "scope_notes": "Refers to the architectural construction completion of the Eiffel Tower in Paris, France."
  },
  "retrieval": null,
  "models_used": {
    "analysis": "gemini-3.8-flash"
  },
  "usage": {
    "prompt_tokens": 459,
    "output_tokens": 284,
    "total_tokens": 743,
    "video_tokens": null,
    "audio_tokens": null
  }
}
```

## Review (by hand)

- status: 
- reviewer: 
- quote_checked_against_video_or_transcript: 
- speaker_checked: 
- sources_opened: 
- verdict_agrees: 
- notes: 
