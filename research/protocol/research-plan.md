# Research plan (frozen before experiment execution)

Frozen 2026-07-17 at repository commit `94096fdd`, after the evidence inventory and before any experiment ran. Working title:

> **When Should a Multimodal Laboratory Assistant Ask for Help? Evaluating a Deterministic, Uncertainty-Aware Human-in-the-Loop Policy for Protocol-Grounded Task Assistance**

## 1. Research questions

- **RQ1 (primary).** How does LabJarvis's deterministic, uncertainty-aware human-in-the-loop orchestration policy affect procedural reliability — unsafe executed actions, incorrectly recorded data, and false reagent acceptances — compared with less constrained orchestration of identical model outputs?
- **RQ2.** Which pipeline stage (speech transcription, command interpretation, label recognition, or decision policy) contributes most to end-to-end failure on the benchmark?
- **RQ3.** What safety–usability trade-off does the policy introduce, measured as human confirmations and abstentions incurred per prevented error, and how sensitive is the benefit to operator attentiveness?

## 2. Hypotheses

- **H1.** On identical inputs, the deterministically constrained conditions (B, C) execute fewer invalid or incorrect protocol actions than the model-led baseline (A).
- **H2.** The equality-or-abstain reagent policy (C) produces a lower false-accept rate than similarity-threshold acceptance (A), at the cost of a higher abstention/confirmation load.
- **H3.** The reliability advantage of condition C over B depends on operator attentiveness: with an attentive operator C prevents additional errors; with an operator who always accepts, C's outcomes approach B's.

All three are testable with the planned benchmark. No hypothesis about real-LLM interpretation quality is retained, because a real LLM runtime (Ollama) is unavailable in this environment (see unresolved items).

## 3. Conditions

All conditions consume **the same recorded model outputs per sample** (same OCR text, same transcript, same parsed proposal), so differences measure the orchestration policy only. Weaker conditions run exclusively in the offline research harness (`research/experiments/`); the production application is never weakened.

| | A: model-led baseline | B: deterministic constraints | C: full implemented policy |
|---|---|---|---|
| Command schema/unit validation | none (proposal executed if syntactically usable) | `validate_and_normalise_command` | same as B |
| Protocol state gating | none | state machine allowed-transition check | same as B |
| Confidence gate (0.75) | no | no | yes |
| Human confirmation of consequential actions | no | no | yes (operator decision) |
| Reagent decision | accept if similarity >= 72, else reject; no abstention | accept if similarity >= 92, else reject; no abstention | production `classify_reagent`: exact/alias equality -> MATCH; near-miss alpha -> MISMATCH; empty/non-alpha mid-band -> UNCERTAIN (abstain to human) |

Condition C is the shipped behaviour (service.py + reagent.py, thresholds 0.75/92/72). A and B are ablations implemented in the research harness by calling the same production domain functions with policy toggles. Operator models for C: (i) *attentive* — confirms correct proposals, rejects incorrect ones (primary analysis); (ii) *always-accept* — automation-bias worst case (sensitivity analysis for H3).

## 4. Contributions targeted

1. A reproducible, protocol-grounded multimodal assistant architecture in which pre-trained speech and vision models propose and deterministic code disposes (artifact: the LabJarvis repository).
2. A precise description of the implemented interpretable intervention cascade (reject / clarify / confirm / abstain) — not a numeric risk score, which the system deliberately does not have.
3. A clearly-synthetic, redistributable benchmark (text commands, synthetic-speech audio, rendered label images, orchestration scenarios) plus a configuration-driven harness and analysis scripts.
4. An empirical ablation (A/B/C) using a real speech model (faster-whisper `small.en`) and a real OCR model (EasyOCR `english_g2`) on that benchmark, including risk–coverage analysis of the reagent policy and an operator-attentiveness sensitivity analysis.
5. A failure taxonomy and design recommendations for human-controlled multimodal procedural assistants.

## 5. Experiments

- **E1 speech.** Real faster-whisper `small.en` (CPU int8) on synthetic-speech WAV clips (Windows SAPI voices; clearly labelled synthetic speech, no human speakers). Metrics: WER, CER, latency, RTF; downstream command-pipeline success from audio.
- **E2 interpretation + validation.** The deterministic rule-grammar interpreter (a real shipped component; explicitly *not* LLM evidence) on the text-command set; conditions A vs B/C on the same proposals. Metrics: intent accuracy, slot P/R/F1, exact match, unsupported-rejection rate, unsafe-action (executed-invalid) rate.
- **E3 label verification.** Real EasyOCR on rendered label images; reagent decision under A/B/C. Metrics: OCR CER, verification accuracy, false-accept rate, abstention rate, coverage, selective accuracy, match P/R/F1, risk–coverage curves over the similarity threshold.
- **E4 orchestration scenarios.** Scripted end-to-end scenarios replayed against the real domain state machine + validation + policy under A/B/C with both operator models. Metrics: invalid-transition executions, incorrect persisted records, task success, confirmations, abstentions, interventions per prevented error.

## 6. Datasets (all newly constructed, clearly synthetic, redistributable)

See `research/data/DATA_CARD.md` and manifests under `research/data/manifests/`. Sizes (target): text >= 120 commands; speech >= 60 clips (2 voices x clean/noise-augmented); vision >= 100 images (match / similar-name mismatch / misspelling / rotation / blur / low-contrast / occlusion / blank); scenarios >= 24. No human participants, no real chemicals, no personal data. Synthetic speech is labelled and analysed as synthetic speech only.

## 7. Threshold provenance

Decision thresholds (0.75 confidence; 92/72 similarity) are pre-existing production defaults committed before this study (commit `94096fd` and earlier); they were **not tuned on the benchmark**. Threshold sweeps appear only as descriptive sensitivity/risk–coverage analysis, not as tuning.

## 8. Out of scope / known blockers

- Real-LLM (Qwen via Ollama) benchmarking: blocked (no Ollama runtime installable unattended in this environment).
- Human-participant usability results: no ethically collected data exist; the repository's study plan is future work.
- Real chemical-content verification: out of scope by design; visible-text similarity only.

## 9. Analysis

Statistical procedures are predeclared in `analysis-plan.md` (same directory) and were fixed before final experiment execution.
