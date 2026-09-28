# Models used in this study

## Real pre-trained models (executed)

| Domain | Model | Runtime | Version | Weights source | Licence |
|---|---|---|---|---|---|
| Speech | Whisper `small.en` (CTranslate2 conversion `Systran/faster-whisper-small.en`, snapshot `d1d751a5f827`) | faster-whisper 1.2.1 / ctranslate2 4.8.1, CPU, int8 | see `requirements-models.lock` | Hugging Face Hub | MIT |
| Vision (OCR) | EasyOCR detector (CRAFT) + recogniser `english_g2` | easyocr 1.7.2, torch 2.13.0, CPU | see lock file | JaidedAI release server | Apache-2.0 |

Configuration: language `en`, beam size 5, VAD filter on (speech); `detail=1`,
`paragraph=False` (OCR). Confidence signals: mean segment `avg_logprob`
exponentiated and clipped to \([0,1]\) (speech); mean per-detection confidence
(OCR). The speech value is recorded as an adapter diagnostic and is not the
command-confidence gate input. Neither is a calibrated probability and neither
is treated as one.

## Deterministic components under test (not ML models)

- Rule-grammar interpreter `FakeTextUnderstanding` (`fake-text-rules-v1`): a
  shipped deterministic regex grammar used by the application's fake mode. It
  is evaluated as a rule-based interpreter. Recognised outputs receive
  confidence 0.99 and unsupported outputs 0.00. Its results are not evidence
  about a language model.
- Domain validation and policy: `validate_and_normalise_command`,
  `classify_reagent`, session/step state machines (production code, commit
  `94096fd`).

## Not executed (blocker)

- Ollama + Qwen2.5 1.5B Instruct (`qwen2.5:1.5b`), the application's default
  local text model, was **not** benchmarked in the original July study:
  Ollama was unavailable in that environment. The July rule-grammar
  interpretation results therefore do not establish LLM accuracy. September
  post-review model comparisons are separately recorded in
  [`research/post_review/PROVENANCE.md`](../post_review/PROVENANCE.md).

## Hardware/software environment

Recorded per run in each raw result file (`environment` key): Windows 11
(10.0.26200), Python 3.12.13, AMD64 CPU execution; developer laptop with
16 GB RAM and RTX 4050 (GPU unused; CPU inference throughout).
