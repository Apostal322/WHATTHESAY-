# Evaluation protocol

> **No benchmark results have been measured yet.** The numbers in the "Targets"
> column are goals, not results. Run the protocol below with a key to produce
> measured numbers, and report them with the dataset version and model IDs.

## Dataset format

JSON Lines; one item per line, validated by `EvalItem` in
`backend/app/evaluation/metrics.py`.

```json
{
  "id": "vid-001",
  "video_id": "VIDEO_ID_11",
  "timestamp_sec": 755,
  "language": "en",
  "ground_truth_claim": "Exact claim a human heard just before 12:35",
  "claim_type": "factual",
  "reference_verdict": "contradicted",
  "supporting_evidence": [{"url": "https://www.bls.gov/ooh/...", "note": "employment projected to grow"}],
  "notes": "Labeller notes, date checked"
}
```

- **Video items** have `video_id` + `timestamp_sec` and test extraction end to end.
- **Claim-only items** omit them and test verification only.
- The extracted claim, verdict and sources are **run outputs** (`EvalRecord`),
  written to `eval/runs/<timestamp>.jsonl`. They are never part of the dataset.

`eval/dataset.example.jsonl` contains six claim-only items (EN/RU) with
long-established reference verdicts: facts, myths, an opinion and a prediction.
It deliberately contains **no video items**, because ground truth for a video
must come from a human who watched it. To build a real set:

1. Pick 30–50 public videos across topics (science, economics, health,
   technology) and both languages.
2. For each, choose 1–3 timestamps right after a statement. Write the claim
   verbatim, its type, and a reference verdict with 1–3 evidence URLs that you read.
3. Include hard cases: opinions phrased as facts, predictions, statements with
   wrong numbers, claims needing a time/geography qualifier, and moments with no claim.
4. Record the date checked; verdicts on current events can change.

## Running

```bash
python scripts/run_eval.py eval/dataset.example.jsonl                         # verification only
python scripts/run_eval.py eval/my_videos.jsonl --verify-from extracted       # true end-to-end
```

## Metrics

| Metric | Definition | Target (not measured) |
|---|---|---|
| Claim extraction accuracy | Share of video items whose extracted claim has token-F1 ≥ 0.5 against the ground truth. F1 is a proxy; spot-check misses by hand. | ≥ 0.7 |
| Claim type accuracy | Extracted `claim_type` equals the label. | ≥ 0.8 |
| Evidence retrieval relevance | (a) share of returned sources that the evaluator did not mark `irrelevant`; (b) reference-domain recall: share of labelled evidence domains that appear among returned sources. Validate (a) by hand on a sample, since it is self-assessed. | (a) ≥ 0.7 |
| Verdict accuracy | Predicted verdict equals the reference verdict (5-way). | ≥ 0.6 |
| **False confident verdict rate** | Share of items where the system said `supported`/`contradicted` but the reference says otherwise, including polarity flips. **This is the primary safety metric**: an `insufficient_evidence` answer on a supported claim is a miss, not a false confident verdict. | ≤ 0.05 |
| Citation validity | Share of returned source URLs that pass `is_safe_display_url`. Optionally open a sample to confirm reachability **and** that the page backs the attributed excerpt; a reachable URL alone proves nothing. | 1.0 by construction; relevance ≥ 0.9 by hand |
| End-to-end latency | p50/p90 of extraction and verification `latency_ms`. | p50 under 30 s total |
| Cost per verification | `estimated_cost_usd` (requires configured prices) plus mean total tokens; add search-grounding charges from billing. | Report, no target yet |

## Interpreting results

- Report false confident rate together with verdict accuracy. A system that always
  answers `insufficient_evidence` has a 0 false confident rate and is useless.
- Model-reported stances and confidence are **not calibrated probabilities**,
  so the product shows no confidence score. Calibration needs a larger labelled
  set and a held-out split.
- Re-run after any prompt, model or window change, and keep the run files for
  comparison.

## Reference records, offline fixtures and propositions

Real-world cases get a *reference record* (`eval/reference/<id>.json`): the claim split into
propositions (attribution, temporal, characterization, legal effect, ...) with a reference verdict
each, the evidence with its status kept apart (externally located, inspected by the application,
human-approved, AI interpretation), and dated events for chronology checks. Scores against a record
are **provisional** until a person sets `review.status: human_verified`; the schema refuses that from
an AI reviewer.

An *offline evidence fixture* (`eval/fixtures/<id>/fixture.json`) stores short verbatim excerpts that
the application itself captured (`scripts/capture_evidence.py`) and replays them through the real
pipeline, one proposition at a time (`scripts/run_fixture_case.py`). Results from it are labelled
"live evaluation of offline evidence": they test reasoning over a fixed evidence set, never retrieval,
and are not live fact-checks.
