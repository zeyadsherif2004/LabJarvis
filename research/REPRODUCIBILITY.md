# Reproducibility guide for the July evaluation

> Scope: the July 2026 evaluation at commit `d5b7f04`. The final-report
> evidence of 2026-09-26/27 (model selection, held-out sets, GPU runs, hosted
> comparisons) is reproduced with the commands in `research/README.md` and
> recorded per run in `research/interpreter/results/` and
> `research/final_report_evidence/`. Since then, `.venv` may carry a CUDA
> build of PyTorch (`2.13.0+cu130`) installed as described in
> `docs/models/gpu-setup.md`; the lock pins the CPU build `torch==2.13.0`.

## Environment

- Windows 11 (10.0.26200), Python 3.12.13, Node not required for the study.
- Audited hardware: laptop, 16 GB RAM, AMD64 CPU, NVIDIA RTX 4050 6 GB
  (unused — all inference ran on CPU).
- Pinned dependencies: `apps/api/requirements.lock` +
  `apps/api/requirements-models.lock` (faster-whisper 1.2.1, ctranslate2 4.8.1,
  easyocr 1.7.2, torch 2.13.0). `matplotlib` and `numpy` are needed for
  analysis.
- Model weights (downloaded once, not redistributed):
  `Systran/faster-whisper-small.en` (snapshot `d1d751a5f827`, ~486 MB) and
  EasyOCR CRAFT detector + `english_g2` recogniser, via
  `.venv/Scripts/python scripts/setup_models.py download`.
- Repository commit for all runs: `d5b7f04` (the July evaluation was originally
  run at `94096fd`; the later version corrected the reported analysis).
- Random seed: 20260717 (noise augmentation and all bootstrap resampling).

## Deterministic vs non-deterministic stages

Deterministic: dataset generation given the same fonts/voices, rule-grammar
interpretation, validation, state machine, policy layers, all statistics
(seeded). Near-deterministic: faster-whisper (beam search, temperature 0) and
EasyOCR on identical files produce stable outputs in practice, but bitwise
stability across CPU/BLAS builds is not guaranteed; latency and RSS vary with
hardware and load. SAPI voice rendering differs across Windows voice versions,
so distributed WAV files should be preferred over re-rendering when replicating
speech results exactly.

## Steps

1. `python scripts/tasks.py setup`
2. `python scripts/tasks.py models` then
   `.venv/Scripts/python scripts/setup_models.py download`
   (Ollama not required for this study; its absence is a recorded blocker for
   LLM benchmarking.)
3. `.venv/Scripts/python -m pip install matplotlib`
4. Run the generator, experiment, and analysis commands listed in
   [`README.md`](README.md). The analysis writes processed tables under
   `research/results/processed/`; its vector charts live under
   `research/results/figures/`.

Expected runtime on the audited machine: dataset generation < 2 min; E2/E4
seconds; E3 ~2 min; E1 ~4 min; analysis < 1 min.

## Known reproduction limitations

- SAPI voices and Windows font files are Windows-specific; on other platforms
  reuse the committed WAV/PNG media instead of regenerating.
- Latency/RSS figures are hardware-specific context measurements, not claims.
- Whisper/EasyOCR minor version changes can shift error rates; keep the pinned
  lock file.
