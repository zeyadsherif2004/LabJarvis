# LabJarvis research package

Research code and evidence for LabJarvis. Nothing in this directory changes the
production application's behaviour. The July controlled evaluation and the
September extensions have separate inputs, results and provenance; I use both
to assess the system in the university report. See
[`REPRODUCIBILITY.md`](REPRODUCIBILITY.md) and the
[`final-report evidence index`](final_report_evidence/INDEX.md).

## Layout

```
research/
├── protocol/        research-plan.md, analysis-plan.md (frozen before runs)
├── evidence/        experiment registry, protocol deviations, figure/table provenance
├── data/            generators, manifests, media, DATA_CARD.md
├── models/          MODEL_CARD.md
├── experiments/     run_e1..e4 runners + common.py (condition policies)
├── analysis/        analyze.py (stats/tables), figures.py (vector figures)
├── results/raw/     machine-written experiment output (never hand-edited)
├── results/processed/  script-derived tables and stats-tests.json
├── literature/      search strategy, source ledger, synthesis matrix, notes
├── post_review/     September evaluation extensions (PROVENANCE.md)
│
│   Additional final-report evidence (2026-09-26/27)
├── data/heldout/    public human-written held-out sets A and B + SOURCES
├── interpreter/     model-selection sweep: run_sweep, analyze_sweep, analyze_d7,
│                    check_splits, configs/, results/ (FREEZE.json)
├── grounding/       grounded-answer generalisation (public protocols)
├── speech/          ASR conditioning, wake-phrase and voice-only evaluations
├── vision/          multi-frame ablation, live-scan simulation
├── live/            pricing.json and d8_hosted.py (paid, ledger-capped)
├── real_media/      collection templates, freeze and chain runner (run_chain.py)
├── study/           user-study plan generation, trial extraction, analysis
└── final_report_evidence/  generated tables/figures/JSON + INDEX.md (hashed)
```

Final-report evidence is regenerated with
`python -m research.interpreter.analyze_sweep`,
`python -m research.interpreter.analyze_d7`,
`python -m research.grounding.evaluate` and
`python -m research.final_report_evidence.build_evidence`; paid runs and all
deviations are recorded in `evidence/protocol-deviations.md`.
The versioned local-OCR scan evaluations are regenerated with
`python -m research.vision.live_scan_simulation --gpu`; this writes a new
`hands-free/live-scan-simulation-v3.json` while preserving the v1 and v2 files.

## Reproduce the July evaluation

See `REPRODUCIBILITY.md`. Short form (from the repository root, after
`python scripts/tasks.py setup` and `python scripts/tasks.py models`):

```powershell
.venv/Scripts/python research/data/generators/generate_text_dataset.py
.venv/Scripts/python research/data/generators/generate_speech_dataset.py   # Windows SAPI required
.venv/Scripts/python research/data/generators/generate_vision_dataset.py
.venv/Scripts/python research/data/generators/generate_scenarios.py
.venv/Scripts/python research/experiments/run_e2_text.py
.venv/Scripts/python research/experiments/run_e3_vision.py                 # real EasyOCR
.venv/Scripts/python research/experiments/run_e1_speech.py                 # real faster-whisper
.venv/Scripts/python research/experiments/run_e4_scenarios.py
.venv/Scripts/python research/analysis/analyze.py
.venv/Scripts/python research/analysis/figures.py
```

The two vector figures above are written to `research/results/figures/`.
Original raw results are unchanged. September results and their frozen
configuration are documented in
[`post_review/PROVENANCE.md`](post_review/PROVENANCE.md).

The research fixtures are synthetic and redistributable under the repository
licence, subject to the model and Windows system-font details in
[`data/DATA_CARD.md`](data/DATA_CARD.md). No model weights are bundled.
The real-world session export contains my own speech transcripts, but no raw
recording or participant data.
