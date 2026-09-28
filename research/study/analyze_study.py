"""Descriptive analysis of the formative confirmation-design study.

For n <= 8 participants the analysis is descriptive only (exact counts,
Wilson 95% intervals, medians); no significance tests are run, as the study
protocol specifies.

Inputs (anonymised; participant codes P01...):

* ``trials.csv`` - from ``extract_trials.py``;
* ``questionnaires.csv`` - participant, condition, sus_1..sus_10 (1-5),
  tlx_mental, tlx_physical, tlx_temporal, tlx_performance, tlx_effort,
  tlx_frustration (0-100), match_comprehension (correct/incorrect);
* ``issues.csv`` - issue_id, participant, theme, severity (1-4), description;
* ``iterations.csv`` (optional) - issue_id, change, files, tests, retest, outcome.

Outputs ``summary.json``, ``summary.md`` and figures in ``--out``.

    python -m research.study.analyze_study --data <private dir> --out <evidence dir>
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any

SUS_ITEMS = [f"sus_{index}" for index in range(1, 11)]
TLX_ITEMS = [
    "tlx_mental",
    "tlx_physical",
    "tlx_temporal",
    "tlx_performance",
    "tlx_effort",
    "tlx_frustration",
]


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (robust at small n)."""

    if total == 0:
        return (0.0, 1.0)
    phat = successes / total
    denominator = 1 + z * z / total
    centre = (phat + z * z / (2 * total)) / denominator
    half = z * math.sqrt(phat * (1 - phat) / total + z * z / (4 * total * total)) / denominator
    return (max(0.0, centre - half), min(1.0, centre + half))


def sus_score(responses: dict[str, str]) -> float:
    """Standard SUS: odd items (x-1), even items (5-x), sum x 2.5 -> 0-100."""

    total = 0
    for index in range(1, 11):
        value = int(responses[f"sus_{index}"])
        if not 1 <= value <= 5:
            raise ValueError("SUS responses must be 1-5")
        total += (value - 1) if index % 2 == 1 else (5 - value)
    return total * 2.5


def raw_tlx(responses: dict[str, str]) -> float:
    """Raw (unweighted) NASA-TLX: the mean of the six subscales, 0-100."""

    values = [float(responses[item]) for item in TLX_ITEMS]
    if any(not 0 <= value <= 100 for value in values):
        raise ValueError("TLX responses must be 0-100")
    return sum(values) / len(values)


def _read(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def analyse(data: Path) -> dict[str, Any]:
    trials = _read(data / "trials.csv")
    questionnaires = _read(data / "questionnaires.csv")
    issues = _read(data / "issues.csv")
    iterations = _read(data / "iterations.csv")
    participants = sorted({row["participant"] for row in trials})
    by_condition: dict[str, dict[str, Any]] = {}
    for condition in sorted({row["condition"] for row in trials}):
        rows = [row for row in trials if row["condition"] == condition]
        seeded = [row for row in rows if row["injected"] in {"True", "true", "1"}]
        genuine = [row for row in rows if row not in seeded]
        detected = sum(row["decision"] == "rejected" for row in seeded)
        false_rejections = sum(row["decision"] == "rejected" for row in genuine)
        times = [float(row["confirmation_ms"]) for row in rows if row["confirmation_ms"]]
        by_condition[condition] = {
            "trials": len(rows),
            "seeded_errors": len(seeded),
            "seeded_detected": detected,
            "detection_rate": detected / len(seeded) if seeded else None,
            "detection_wilson_95": wilson(detected, len(seeded)),
            "missed_seeded_errors": len(seeded) - detected,
            "genuine_trials": len(genuine),
            "false_rejections": false_rejections,
            "false_rejection_wilson_95": wilson(false_rejections, len(genuine)),
            "confirmation_ms_median": median(times) if times else None,
            "confirmation_ms_range": [min(times), max(times)] if times else None,
        }
    sus: dict[str, list[float]] = defaultdict(list)
    tlx: dict[str, list[float]] = defaultdict(list)
    comprehension = defaultdict(lambda: [0, 0])
    for row in questionnaires:
        sus[row["condition"]].append(sus_score(row))
        tlx[row["condition"]].append(raw_tlx(row))
        comprehension[row["condition"]][1] += 1
        comprehension[row["condition"]][0] += row.get("match_comprehension") == "correct"
    themes: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "max_severity": 0})
    for issue in issues:
        theme = themes[issue["theme"]]
        theme["count"] += 1
        theme["max_severity"] = max(theme["max_severity"], int(issue["severity"]))
    return {
        "evidence_class": "human study (formative, descriptive)",
        "participants": len(participants),
        "analysis": "descriptive only; exact counts with Wilson 95% intervals; no significance tests",
        "conditions": by_condition,
        "sus": {k: {"n": len(v), "median": median(v), "min": min(v), "max": max(v)} for k, v in sus.items()},
        "raw_tlx": {k: {"n": len(v), "median": median(v), "min": min(v), "max": max(v)} for k, v in tlx.items()},
        "match_comprehension": {k: {"correct": v[0], "n": v[1], "wilson_95": wilson(v[0], v[1])} for k, v in comprehension.items()},
        "issue_themes": dict(sorted(themes.items(), key=lambda item: (-item[1]["max_severity"], -item[1]["count"]))),
        "issues_ranked": sorted(issues, key=lambda issue: -int(issue["severity"])),
        "iterations": iterations,
    }


def markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Formative study results (generated)",
        "",
        f"Participants: {summary['participants']}. {summary['analysis']}.",
        "",
        "| Condition | Seeded errors caught | Detection (Wilson 95%) | False rejections | Median confirmation time |",
        "|---|---:|---|---:|---:|",
    ]
    for condition, stats in summary["conditions"].items():
        low, high = stats["detection_wilson_95"]
        rate = "n/a" if stats["detection_rate"] is None else f"{stats['detection_rate']:.2f}"
        lines.append(
            f"| {condition} | {stats['seeded_detected']}/{stats['seeded_errors']} | {rate} "
            f"[{low:.2f}, {high:.2f}] | {stats['false_rejections']}/{stats['genuine_trials']} | "
            f"{stats['confirmation_ms_median']} ms |"
        )
    lines += ["", "| Condition | SUS median (range) | Raw TLX median (range) |", "|---|---|---|"]
    for condition in summary["sus"]:
        s = summary["sus"][condition]
        t = summary["raw_tlx"].get(condition, {"median": "n/a", "min": "", "max": ""})
        lines.append(f"| {condition} | {s['median']} ({s['min']}-{s['max']}) | {t['median']} ({t['min']}-{t['max']}) |")
    lines += ["", "## Issues by theme (highest severity first)", "", "| Theme | Count | Max severity |", "|---|---:|---:|"]
    for theme, stats in summary["issue_themes"].items():
        lines.append(f"| {theme} | {stats['count']} | {stats['max_severity']} |")
    if summary["iterations"]:
        lines += ["", "## Issue -> change -> retest", "", "| Issue | Change | Files | Tests | Retest | Outcome |", "|---|---|---|---|---|---|"]
        for row in summary["iterations"]:
            lines.append(
                f"| {row['issue_id']} | {row['change']} | {row['files']} | {row['tests']} | "
                f"{row['retest']} | {row['outcome']} |"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    summary = analyse(args.data)
    if summary["participants"] == 0:
        print("no study data found - nothing to analyse (evidence dependency)")
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (args.out / "summary.md").write_text(markdown(summary), encoding="utf-8")
    print(markdown(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
