# Evaluation harness

> The text-interpreter model selection reported in the final evidence was run
> with `research/interpreter/run_sweep.py` (see
> [the model-selection record](../docs/evaluation/model-selection-record.md)).
> This general harness remains the path for real speech/photo candidate runs
> and the offline safety gate (`python scripts/tasks.py evaluate-safety`).

This directory separates model evaluation from software tests. A passing unit test shows that LabJarvis behaves as implemented; it does not show that a real speech, text, or OCR model performs adequately in a lab context.

## What the runner measures

| Domain | Primary metrics | Context metric or safety signal |
|---|---|---|
| Speech | word error rate (WER), character error rate (CER) | latency, process RSS, and real-time factor when clip duration is supplied |
| Text | intent accuracy, slot precision/recall/F1, exact slot-set match, schema-valid rate | unsupported rejection, unsafe-action rate, ambiguity detection, latency, process RSS |
| Vision | CER, exact visible-text accuracy, verification accuracy, match precision/recall/F1 | false-accept rate, uncertainty rate, latency, process RSS |

WER and CER use transparent Levenshtein edit distance after case/whitespace normalisation. Exact slot match is deliberately strict. A vision false accept means the deterministic comparison returned `MATCH` when the labelled expected outcome was not `MATCH`; this should be treated as more serious than a conservative `UNCERTAIN` result.

## Safe development check

```powershell
python scripts/tasks.py evaluate
```

This uses deterministic `FAKE:` records only. Its output is written under `evaluation/results/` and contains:

- a JSON run record with configuration hash, environment, candidates, per-sample hashes, failures, and metrics;
- a CSV summary suitable for inspection or later report charting.

The result is hard-labelled `synthetic_development_only` and `eligible_for_final_claims: false`. Perfect fake-adapter scores only verify the harness; they are not model benchmarks.

## Real candidate comparison

1. Read [the dataset guide](datasets/README.md).
2. Collect or create consented, non-sensitive media outside Git.
3. Add JSONL manifests to the three `student-*` files. Media paths are resolved relative to each manifest.
4. Install and download every candidate in `configs/local-candidates.yaml`.
5. Run:

   ```powershell
   .venv/Scripts/python -m labjarvis.evaluation.run `
     --config evaluation/configs/local-candidates.yaml `
     --output evaluation/results/local-run-01
   ```

6. Repeat warm and cold runs on the same named hardware. Preserve raw result files outside Git if they contain sensitive predictions.
7. Inspect failures, not only aggregate means. Record the exact Ollama digest, model revisions, compute types, thresholds, ambient/noise/image conditions, and any excluded samples.

The included candidates compare three Whisper sizes, two Qwen sizes, and two
EasyOCR recognition networks. I treated them as starting points, not a
presumption that the largest model would win. My selection rule is the
smallest candidate meeting the accuracy, false-accept, latency, memory, and
recovery criteria fixed before looking at results.

## Result integrity

- Do not edit generated JSON to improve a score.
- Commit configs and code; normally keep raw/derived results out of Git until privacy and repository-size review.
- Never label synthetic or development fixtures as participant data.
- A failed inference receives conservative failure metrics and remains visible in `failure_count`.
- `include_predictions: false` is the privacy-aware default for real runs. Input hashes allow run comparison without copying raw transcripts or images into results.
- The student remains responsible for checking licences, participant consent, institutional ethics requirements, and the statistical claims made in the final report.
