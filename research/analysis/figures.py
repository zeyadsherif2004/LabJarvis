"""Generate evaluation figures (vector PDF) from processed results."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "results" / "processed"
FIGURES = ROOT / "results" / "figures"

plt.rcParams.update({
    "font.size": 8,
    "font.family": "serif",
    "axes.linewidth": 0.6,
    "figure.dpi": 300,
    # Embed TrueType (42) rather than the matplotlib default Type 3 fonts so
    # the figures remain readable in exported reports.
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
})


def read_csv(name: str) -> list[dict[str, str]]:
    with (PROCESSED / name).open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def acceptance_characteristic() -> None:
    sweep = read_csv("e3-threshold-sweep.csv")
    thresholds = [float(row["threshold"]) for row in sweep]
    fa = [float(row["false_accept_rate"]) for row in sweep]
    ta = [float(row["true_accept_rate"]) for row in sweep]
    summary = {row["policy"]: row for row in read_csv("e3-condition-summary.csv")}

    fig, ax = plt.subplots(figsize=(3.45, 2.4))
    ax.plot(thresholds, fa, color="0.15", linewidth=1.4, label="False-accept rate")
    ax.plot(thresholds, ta, color="0.55", linewidth=1.4, linestyle="--",
            label="True-accept rate")
    for policy, threshold, marker in (("A", 72.0, "o"), ("B", 92.0, "s")):
        row = summary[policy]
        ax.plot(threshold, float(row["false_accept_rate"]), marker, color="0.15",
                markersize=5, markerfacecolor="white")
        ax.annotate(policy, (threshold, float(row["false_accept_rate"])),
                    textcoords="offset points", xytext=(4, 5))
    row_t = summary["T93.5"]
    ax.plot(93.5, float(row_t["false_accept_rate"]), "D", color="0.15",
            markersize=5, markerfacecolor="white")
    ax.annotate("C / T*", (93.5, float(row_t["false_accept_rate"])),
                textcoords="offset points", xytext=(-30, 6))
    ax.set_xlabel("Similarity acceptance threshold")
    ax.set_ylabel("Rate")
    ax.set_xlim(60, 100.5)
    ax.set_ylim(0, 1.02)
    ax.legend(frameon=False, loc="center left", fontsize=7)
    fig.tight_layout()
    FIGURES.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURES / "fig-acceptance-characteristic.pdf")
    plt.close(fig)


def e4_outcomes() -> None:
    rows = read_csv("e4-condition-summary.csv")
    labels = {"A": "A", "B": "B", "C_attentive": "C (attentive)",
              "C_always_accept": "C (always-accept)"}
    conditions = [row["condition"] for row in rows]
    success = [float(row["success_rate"]) for row in rows]
    low = [float(row["success_rate"]) - float(row["wilson_low"]) for row in rows]
    high = [float(row["wilson_high"]) - float(row["success_rate"]) for row in rows]
    unsafe = [int(row["unsafe_events"]) for row in rows]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(3.45, 2.1))
    x = range(len(conditions))
    ax1.bar(x, success, yerr=[low, high], capsize=3, color="0.55", edgecolor="0.15",
            linewidth=0.6, error_kw={"linewidth": 0.8})
    ax1.set_xticks(list(x))
    ax1.set_xticklabels([labels[c] for c in conditions], rotation=30, ha="right",
                        fontsize=6.5)
    ax1.set_ylabel("Scenario success rate")
    ax1.set_ylim(0, 1.05)
    ax2.bar(x, unsafe, color="0.25", edgecolor="0.1", linewidth=0.6)
    ax2.set_xticks(list(x))
    ax2.set_xticklabels([labels[c] for c in conditions], rotation=30, ha="right",
                        fontsize=6.5)
    ax2.set_ylabel("Unsafe events (24 scenarios)")
    fig.tight_layout()
    fig.savefig(FIGURES / "fig-e4-outcomes.pdf")
    plt.close(fig)


def provenance() -> None:
    record = {
        "fig-acceptance-characteristic.pdf": {
            "raw": "results/raw/e3-vision-results.json",
            "processed": ["e3-threshold-sweep.csv", "e3-condition-summary.csv"],
            "script": "analysis/figures.py:acceptance_characteristic",
        },
        "fig-e4-outcomes.pdf": {
            "raw": "results/raw/e4-scenario-results.json",
            "processed": ["e4-condition-summary.csv"],
            "script": "analysis/figures.py:e4_outcomes",
        },
    }
    (FIGURES / "provenance.json").write_text(
        json.dumps(record, indent=2, sort_keys=True), encoding="utf-8"
    )


def main() -> None:
    acceptance_characteristic()
    e4_outcomes()
    provenance()
    print(FIGURES)


if __name__ == "__main__":
    main()
