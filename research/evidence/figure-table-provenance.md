# Figure and table provenance

Every evaluation figure/table derives from raw machine-written results via scripts.
Raw files were never hand-edited. Reproduce with:

```powershell
.venv/Scripts/python research/analysis/analyze.py
.venv/Scripts/python research/analysis/figures.py
```

| Evaluation asset | Raw source | Processing script | Output |
|---|---|---|---|
| Table: interpretation/validation condition comparison (E2) | `results/raw/e2-text-results.json` | `analysis/analyze.py::analyse_e2` | `results/processed/e2-condition-summary.csv` |
| Table: reagent policy comparison (E3) | `results/raw/e3-vision-results.json` | `analysis/analyze.py::analyse_e3` | `results/processed/e3-condition-summary.csv` |
| Table: scenario outcomes (E4) | `results/raw/e4-scenario-results.json` | `analysis/analyze.py::analyse_e4` | `results/processed/e4-condition-summary.csv` |
| Table: speech metrics (E1) | `results/raw/e1-speech-results.json` | `analysis/analyze.py::analyse_e1` | `results/processed/e1-speech-summary.csv` |
| Table/text: failure taxonomy | all four raw files | `analysis/analyze.py::failure_taxonomy` | `results/processed/failure-taxonomy.csv` |
| Statistical tests quoted in text | all four raw files | `analysis/analyze.py` | `results/processed/stats-tests.json` |
| Fig.: acceptance operating characteristic | `results/raw/e3-vision-results.json` | `analysis/figures.py::acceptance_characteristic` | `results/figures/fig-acceptance-characteristic.pdf` |
| Fig.: scenario outcomes | `results/raw/e4-scenario-results.json` | `analysis/figures.py::e4_outcomes` | `results/figures/fig-e4-outcomes.pdf` |

Screenshot evidence in the repository README derives from
`python scripts/tasks.py screenshots` (fake mode) and is separate from
the evaluation figures.
