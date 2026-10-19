# Annotation guide: reviewing real cases one by one

Accuracy can only be measured against labels a person wrote after watching the video and
reading the sources. This guide turns `eval/annotation/cases.jsonl` into that ground truth.

## What a case contains

- `machine`: what a model extracted at that moment, with the local run artifact. **It is
  only a starting point.** Never copy it into the human block without checking every word.
- `review_hints`: questions to answer. They are not labels.
- `human`: empty until you fill it in.

## Steps for one case

1. Open the video at the timestamp (for example `https://www.youtube.com/watch?v=VgsC_aBquUE&t=1200s`)
   and listen to the 45 seconds before it.
2. Fill in the `human` block:

   | Field | What to write |
   |---|---|
   | `exact_words` | The words actually spoken, transcribed by you (use a transcript if available and say which) |
   | `speaker` | Who said them, as shown or announced in the video |
   | `claim` | The checkable claim a viewer would want verified, self-contained |
   | `claim_type` | `factual`, `prediction`, `opinion`, `ambiguous` or `non_verifiable`. Religious doctrine and philosophical interpretation are `opinion` or `non_verifiable`; what a text or person actually said is `factual` |
   | `reference_verdict` | `supported`, `contradicted`, `mixed`, `insufficient_evidence` or `not_verifiable`, judged at the date the statement was made |
   | `evidence` | URLs you opened that justify the verdict, each with a one-line note |
   | `disagreement_notes` | Anything uncertain, and a second reviewer's view if it differs |

3. When everything is filled in and you are confident, set `"status": "human_verified"`,
   `"reviewer"` (your name or handle) and `"reviewed_on"` (YYYY-MM-DD). If two reviewers
   disagree, use `"status": "disputed"` and record both views; disputed cases are not scored.
4. Check progress and export:

   ```bash
   python scripts/annotation_status.py
   python scripts/annotation_status.py --export eval/dataset.human.jsonl
   python scripts/run_eval.py eval/dataset.human.jsonl   # live: costs model calls
   ```

`scripts/run_eval.py` reports accuracy only over human-verified items. Anything else appears
under `provisional_unreviewed_labels` and must not be reported as accuracy.

## Adding cases

Add a line with `id`, `youtube_url`, `timestamp_sec`, `language` and `"human": {"status":
"unreviewed"}`. Aim for 30–50 moments, both English and Russian, including opinions,
attributions ("X said ..."), numbers and historical claims, not only easy facts.
