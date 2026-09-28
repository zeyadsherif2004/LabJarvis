"""Generate post-review tables and reproducible statistics from raw results."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from research.post_review.run_interpreters import load_text_samples

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "research" / "post_review" / "results"
OUTPUT = RESULTS / "processed"
SEED = 20260901
BOOTSTRAP_SAMPLES = 10_000

INTERPRETER_FILES = {
    "Rule grammar": "rule-interpreter.json",
    "Qwen2.5 0.5B": "ollama-qwen2.5-0.5b.json",
    "Qwen2.5 1.5B": "ollama-qwen2.5-1.5b.json",
    "Hosted gpt-5.6-terra": "hosted-gpt-5.6-terra.json",
}
COMPONENTS = (
    "proposal",
    "validation",
    "state",
    "confidence",
    "confirm_attentive",
    "confirm_always_accept",
)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _round(value: float, digits: int = 4) -> float:
    return round(float(value), digits)


def wilson(successes: int, total: int) -> tuple[float, float, float]:
    """Wilson score interval at 95% confidence."""

    if total == 0:
        return 0.0, 0.0, 1.0
    z = 1.959963984540054
    phat = successes / total
    denominator = 1 + z**2 / total
    centre = (phat + z**2 / (2 * total)) / denominator
    margin = z * math.sqrt(
        phat * (1 - phat) / total + z**2 / (4 * total**2)
    ) / denominator
    return phat, max(0.0, centre - margin), min(1.0, centre + margin)


def mcnemar_exact(only_first: int, only_second: int) -> float:
    """Two-sided exact McNemar p-value on discordant pair counts."""

    n = only_first + only_second
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(only_first, only_second) + 1))
    return min(1.0, 2 * tail / 2**n)


def holm(pvalues: dict[str, float]) -> dict[str, float]:
    ordered = sorted(pvalues.items(), key=lambda item: item[1])
    adjusted: dict[str, float] = {}
    running = 0.0
    for rank, (name, pvalue) in enumerate(ordered):
        running = max(running, min(1.0, (len(ordered) - rank) * pvalue))
        adjusted[name] = running
    return adjusted


def bootstrap_diff_ci(
    pairs: Sequence[tuple[int, int]], *, seed_offset: int = 0
) -> tuple[float, float, float]:
    """Paired risk difference (first minus second), percentile bootstrap CI."""

    rng = np.random.default_rng(SEED + seed_offset)
    data = np.asarray(pairs, dtype=float)
    observed = float(data[:, 0].mean() - data[:, 1].mean())
    differences: list[float] = []
    for _ in range(BOOTSTRAP_SAMPLES):
        sample = data[rng.integers(0, len(data), len(data))]
        differences.append(float(sample[:, 0].mean() - sample[:, 1].mean()))
    return observed, float(np.percentile(differences, 2.5)), float(
        np.percentile(differences, 97.5)
    )


def _latency_quantiles(records: Sequence[dict[str, Any]]) -> tuple[int, int, int]:
    latencies = sorted(int(record.get("latency_ms", 0)) for record in records)
    if not latencies:
        return 0, 0, 0
    p50 = latencies[len(latencies) // 2] if len(latencies) % 2 else (
        (latencies[len(latencies) // 2 - 1] + latencies[len(latencies) // 2]) // 2
    )
    p95_index = min(len(latencies) - 1, int(math.ceil(0.95 * len(latencies))) - 1)
    return latencies[0], p50, latencies[p95_index]


def interpreter_summary(
    candidate: dict[str, Any],
    label: str,
    *,
    sample_by_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    records = candidate["records"]
    succeeded = [record for record in records if record.get("status") == "succeeded"]
    sample_count = int(candidate["sample_count"])
    component_counts = {
        component: {"executed": 0, "unsafe_executed": 0, "missed_valid": 0, "confirmations": 0}
        for component in COMPONENTS
    }
    for record in records:
        if "components" in record:
            components = record["components"]
        else:
            if sample_by_id is None:
                raise ValueError("failed records require sample_by_id for component accounting")
            missed_valid = bool(sample_by_id[record["sample_id"]]["action_valid"])
            components = {
                component: {
                    "executed": False,
                    "unsafe_executed": False,
                    "missed_valid": missed_valid,
                    "confirmations": 0,
                }
                for component in COMPONENTS
            }
        for component, outcome in components.items():
            for key in component_counts[component]:
                component_counts[component][key] += int(outcome[key])
    lat_min, p50, p95 = _latency_quantiles(succeeded)
    return {
        "label": label,
        "adapter": candidate["adapter"],
        "model_name": candidate["model_name"],
        "sample_count": sample_count,
        "failure_count": int(candidate["failure_count"]),
        "schema_valid_rate": _round(len(succeeded) / sample_count),
        "intent_accuracy": _round(candidate["metrics"]["intent_accuracy"]),
        "exact_command_accuracy": _round(
            candidate["metrics"]["exact_command_accuracy"]
        ),
        "slot_precision": _round(candidate["metrics"]["slot_precision"]),
        "slot_recall": _round(candidate["metrics"]["slot_recall"]),
        "slot_f1": _round(candidate["metrics"]["slot_f1"]),
        "latency_ms_min": lat_min,
        "latency_ms_p50": p50,
        "latency_ms_p95": p95,
        "component_summary": component_counts,
        "records": records,
    }


def _write_csv(name: str, header: list[str], rows: list[list[Any]]) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with (OUTPUT / name).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def _paired_exact_summary(
    summaries: dict[str, dict[str, Any]], outcome: str
) -> dict[str, Any]:
    names = list(summaries)
    raw: dict[str, float] = {}
    details: dict[str, Any] = {}
    offset = 0
    for index, first in enumerate(names):
        for second in names[index + 1 :]:
            first_flags = [int(record[outcome]) for record in summaries[first]["records"]]
            second_flags = [int(record[outcome]) for record in summaries[second]["records"]]
            only_first = sum(
                left and not right for left, right in zip(first_flags, second_flags)
            )
            only_second = sum(
                right and not left for left, right in zip(first_flags, second_flags)
            )
            pairs = list(zip(first_flags, second_flags))
            difference, low, high = bootstrap_diff_ci(
                pairs, seed_offset=100 + offset
            )
            comparison = f"{first} vs {second}"
            details[comparison] = {
                "discordant_first_only": only_first,
                "discordant_second_only": only_second,
                "p_mcnemar_exact": mcnemar_exact(only_first, only_second),
                "risk_difference": _round(difference),
                "rd_ci95": [_round(low), _round(high)],
            }
            raw[comparison] = details[comparison]["p_mcnemar_exact"]
            offset += 1
    adjusted = holm(raw)
    for name, value in adjusted.items():
        details[name]["p_holm"] = value
    return details


def _interpreter_rows(summaries: dict[str, dict[str, Any]]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for label, summary in summaries.items():
        exact = int(round(summary["exact_command_accuracy"] * summary["sample_count"]))
        intent = int(round(summary["intent_accuracy"] * summary["sample_count"]))
        schema = summary["sample_count"] - summary["failure_count"]
        exact_estimate, exact_low, exact_high = wilson(exact, summary["sample_count"])
        intent_estimate, intent_low, intent_high = wilson(intent, summary["sample_count"])
        rows.append(
            [
                label,
                summary["sample_count"],
                summary["failure_count"],
                schema,
                f"{summary['schema_valid_rate']:.4f}",
                f"{intent_estimate:.4f}",
                f"{intent_low:.4f}",
                f"{intent_high:.4f}",
                f"{exact_estimate:.4f}",
                f"{exact_low:.4f}",
                f"{exact_high:.4f}",
                f"{summary['slot_precision']:.4f}",
                f"{summary['slot_recall']:.4f}",
                f"{summary['slot_f1']:.4f}",
                summary["latency_ms_p50"],
                summary["latency_ms_p95"],
            ]
        )
    return rows


def _interpreter_table(summaries: dict[str, dict[str, Any]]) -> str:
    labels = {
        "Rule grammar": "Rule",
        "Qwen2.5 0.5B": "Qwen 0.5B",
        "Qwen2.5 1.5B": "Qwen 1.5B",
        "Hosted gpt-5.6-terra": "Hosted",
    }
    rows: list[str] = []
    for label, summary in summaries.items():
        component = summary["component_summary"]
        intent = sum(int(record["intent_correct"]) for record in summary["records"])
        exact = sum(int(record["exact_command"]) for record in summary["records"])
        cells = [
            labels[label],
            str(summary["sample_count"] - summary["failure_count"]),
            f"{intent / summary['sample_count'] * 100:.1f}",
            f"{exact / summary['sample_count'] * 100:.1f}",
            *[str(component[name]["unsafe_executed"]) for name in COMPONENTS],
        ]
        rows.append(" & ".join(cells) + r" \\")
    return r"""\begin{table}[!t]
\caption{Post-review interpreter outcomes and additive component gates on the
same 81 commands. P is the unconstrained proposal; V, S, and C are validation,
state, and confidence gates; CO and CA are attentive and always-accept
confirmation. Invalid model output cannot execute.}
\label{tab:postreview}
\centering
\small
\setlength{\tabcolsep}{3pt}
\begin{tabular}{@{}lrrrrrrrrr@{}}
\toprule
 & & \multicolumn{2}{c}{Accuracy (\%)} & \multicolumn{6}{c}{Unsafe executions}\\
\cmidrule(lr){3-4}\cmidrule(lr){5-10}
Interpreter & Valid & Intent & Exact & P & V & S & C & CO & CA\\
\midrule
""" + "\n".join(rows) + r"""
\bottomrule
\end{tabular}
\end{table}
"""


def _component_rows(summaries: dict[str, dict[str, Any]]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for label, summary in summaries.items():
        n = summary["sample_count"]
        for component in COMPONENTS:
            outcome = summary["component_summary"][component]
            unsafe = int(outcome["unsafe_executed"])
            missed = int(outcome["missed_valid"])
            unsafe_rate, unsafe_low, unsafe_high = wilson(unsafe, n)
            missed_rate, missed_low, missed_high = wilson(missed, n)
            rows.append(
                [
                    label,
                    component,
                    n,
                    int(outcome["executed"]),
                    unsafe,
                    f"{unsafe_rate:.4f}",
                    f"{unsafe_low:.4f}",
                    f"{unsafe_high:.4f}",
                    missed,
                    f"{missed_rate:.4f}",
                    f"{missed_low:.4f}",
                    f"{missed_high:.4f}",
                    int(outcome["confirmations"]),
                ]
            )
    return rows


def _protocol_rows() -> list[list[Any]]:
    result = _load(RESULTS / "public-protocol-grounding.json")
    supported = [r for r in result["records"] if r["expected_supported"]]
    unsupported = [r for r in result["records"] if not r["expected_supported"]]
    supported_correct = sum(int(r["correct"]) for r in supported)
    unsupported_correct = sum(int(r["correct"]) for r in unsupported)
    all_correct = supported_correct + unsupported_correct
    all_total = len(result["records"])
    supported_interval = wilson(supported_correct, len(supported))
    unsupported_interval = wilson(unsupported_correct, len(unsupported))
    all_interval = wilson(all_correct, all_total)
    rows: list[list[Any]] = []
    for label, total, successes, interval in (
        ("supported", len(supported), supported_correct, supported_interval),
        ("unsupported", len(unsupported), unsupported_correct, unsupported_interval),
        ("all", all_total, all_correct, all_interval),
    ):
        estimate, low, high = interval
        rows.append(
            [label, total, successes, f"{estimate:.4f}", f"{low:.4f}", f"{high:.4f}"]
        )
    return rows


def _media_rows() -> tuple[list[list[Any]], list[list[Any]]]:
    speech_rows: list[list[Any]] = []
    vision_rows: list[list[Any]] = []
    for path in sorted(RESULTS.glob("speech-*.json")):
        if path.name == "speech-clips.jsonl":
            continue
        result = _load(path)
        candidate = result["candidate"]
        succeeded = [r for r in candidate["records"] if r["status"] == "succeeded"]
        lat_min, p50, p95 = _latency_quantiles(succeeded)
        speech_rows.append(
            [
                candidate["model_name"],
                len(candidate["records"]),
                candidate["failure_count"],
                f"{candidate['metrics']['wer']:.4f}",
                f"{candidate['metrics']['cer']:.4f}",
                p50,
                p95,
                f"{candidate['metrics']['mean_real_time_factor']:.4f}",
            ]
        )
    for path in sorted(RESULTS.glob("vision-*.json")):
        result = _load(path)
        candidate = result["candidate"]
        succeeded = [r for r in candidate["records"] if r["status"] == "succeeded"]
        lat_min, p50, p95 = _latency_quantiles(succeeded)
        vision_rows.append(
            [
                candidate["model_name"],
                len(candidate["records"]),
                candidate["failure_count"],
                f"{candidate['metrics']['cer']:.4f}",
                int(candidate["metrics"]["false_accept_rate"] * len(candidate["records"])),
                p50,
                p95,
            ]
        )
    return speech_rows, vision_rows


def _hash_used_inputs() -> dict[str, Any]:
    paths = {
        "text_manifest": ROOT
        / "research"
        / "data"
        / "manifests"
        / "text-commands.jsonl",
        "public_cases": ROOT / "research" / "post_review" / "public_protocols" / "cases.jsonl",
        "public_sources": ROOT / "research" / "post_review" / "public_protocols" / "sources.json",
    }
    return {
        name: {
            "path": path.relative_to(ROOT).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for name, path in paths.items()
    }


def main() -> int:
    text_manifest = ROOT / "research" / "data" / "manifests" / "text-commands.jsonl"
    samples, _ = load_text_samples(text_manifest)
    sample_by_id = {sample["id"]: sample for sample in samples}
    summaries = {
        label: interpreter_summary(
            _load(RESULTS / name)["candidate"],
            label,
            sample_by_id=sample_by_id,
        )
        for label, name in INTERPRETER_FILES.items()
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    _write_csv(
        "interpreter-summary.csv",
        [
            "interpreter", "n", "failures", "schema_valid", "schema_valid_rate",
            "intent_accuracy", "intent_ci_low", "intent_ci_high",
            "exact_command_accuracy", "exact_ci_low", "exact_ci_high",
            "slot_precision", "slot_recall", "slot_f1", "latency_p50_ms",
            "latency_p95_ms",
        ],
        _interpreter_rows(summaries),
    )
    _write_csv(
        "component-ablations.csv",
        [
            "interpreter", "component", "n", "executed", "unsafe_executed",
            "unsafe_rate", "unsafe_ci_low", "unsafe_ci_high", "missed_valid",
            "missed_rate", "missed_ci_low", "missed_ci_high", "confirmations",
        ],
        _component_rows(summaries),
    )
    (OUTPUT / "postreview-interpreters.tex").write_text(
        _interpreter_table(summaries), encoding="utf-8"
    )
    paired = {
        "exact_command": _paired_exact_summary(summaries, "exact_command"),
        "intent_correct": _paired_exact_summary(summaries, "intent_correct"),
    }
    media_speech, media_vision = _media_rows()
    _write_csv(
        "speech-candidates.csv",
        [
            "model_name", "n", "failures", "wer", "cer", "latency_p50_ms",
            "latency_p95_ms", "mean_real_time_factor",
        ],
        media_speech,
    )
    _write_csv(
        "vision-candidates.csv",
        [
            "model_name", "n", "failures", "cer", "false_accepts",
            "latency_p50_ms", "latency_p95_ms",
        ],
        media_vision,
    )
    _write_csv(
        "protocol-grounding.csv",
        ["case_group", "n", "correct", "accuracy", "ci_low", "ci_high"],
        _protocol_rows(),
    )
    protocol_result = _load(RESULTS / "public-protocol-grounding.json")
    stats = {
        "schema_version": 1,
        "seed": SEED,
        "bootstrap_samples": BOOTSTRAP_SAMPLES,
        "input_hashes": _hash_used_inputs(),
        "interpreter_pairs": paired,
        "protocol_grounding": protocol_result,
        "media": {
            "speech": dict(
                (path.name, _load(path)["candidate"]["metrics"])
                for path in sorted(RESULTS.glob("speech-*.json"))
            ),
            "vision": dict(
                (path.name, _load(path)["candidate"]["metrics"])
                for path in sorted(RESULTS.glob("vision-*.json"))
            ),
        },
    }
    (OUTPUT / "post-review-stats.json").write_text(
        json.dumps(stats, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
