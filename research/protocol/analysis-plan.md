# Statistical analysis plan (predeclared)

Frozen 2026-07-17, before final experiment execution. Random seed for all resampling: **20260717**.

## Outcomes

**Primary outcomes** (per RQ1/H1/H2):

1. Unsafe executed-action rate per condition: proportion of samples on which the condition *executes* an action that the ground truth marks invalid, incorrect, or unsupported (E2 + E4).
2. Reagent false-accept rate per condition: proportion of non-matching labels classified/accepted as MATCH (E3).

**Secondary outcomes:** intent accuracy; slot precision/recall/F1; exact command match; unsupported-rejection rate; OCR CER; verification accuracy; abstention rate; coverage and selective accuracy; WER/CER; latency (median, p95); RTF; scenario task success; confirmations per scenario; interventions per prevented error; per-stage failure attribution (RQ2).

## Units of analysis

Per-sample binary/continuous measurements; scenarios are the unit for E4. All conditions are evaluated on identical samples (paired design).

## Procedures

- Proportions: report with **Wilson score 95% confidence intervals**.
- Paired condition contrasts on binary outcomes (A vs C, B vs C, A vs B): **exact McNemar test** on discordant pairs; report risk difference with a 95% bootstrap CI (10 000 resamples, seed above, percentile method).
- Continuous metrics (WER, CER, latency): mean and median with 95% bootstrap CIs (10 000 resamples); no normality assumption.
- Multiple comparisons: the two primary outcomes x three contrasts are Holm–Bonferroni corrected within each primary outcome family. Secondary outcomes are descriptive (CIs only, no significance claims).
- Significance threshold alpha = 0.05, two-sided. Emphasis on effect sizes and CIs over binary significance, per plan.
- Risk–coverage analysis (E3): sweep the acceptance threshold over the observed similarity-score range; plot false-accept risk vs coverage; the production policy point is marked. Descriptive only.
- Calibration metrics (ECE, Brier) are **not** applied: neither the similarity score nor adapter confidences are claimed to be calibrated probabilities; this is stated in the paper.
- Failure taxonomy: every failed sample/scenario is assigned exactly one primary category (transcription error; name confusion; numeric/unit extraction error; invalid structured output; state conflict; OCR failure; visually similar label; threshold error; policy abstention counted separately as intended behaviour, not failure).

## Exclusion and integrity rules

- No sample is excluded after results are seen. Generation-time filtering only (e.g. a TTS render that produces zero audio) and is logged in the manifests.
- Failed/timed-out inferences receive conservative failure metrics and stay in `failure_count`.
- All raw outputs are machine-written JSON; processed tables/figures are script-derived only (`research/analysis/`).
- Everything runs at fixed pinned versions; environment recorded per run in the experiment registry.
