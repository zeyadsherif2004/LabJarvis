"""Turn sweep results into the model-selection record, tables and figures.

Applies the predeclared acceptance criteria (A1-A5, C1-C2) from
``docs/evaluation/model-selection-protocol.md`` to every configuration, twice:
with the frozen benchmark labels and with the D4 slot adjudication of four
benchmark paraphrases (reported side by side, never merged).

    python -m research.interpreter.analyze_sweep
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from research.experiments.common import semantically_equal_parameters  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "research" / "interpreter" / "results"
OUT = ROOT / "research" / "final_report_evidence" / "model-selection"
ADJUDICATION = ROOT / "research" / "data" / "manifests" / "text-commands-slot-adjudication.json"
BENCHMARK = ROOT / "research" / "data" / "manifests" / "text-commands.jsonl"

THRESHOLDS = {
    "A1_schema_validity": 0.95,
    "A2_exact_benchmark": 0.80,
    "A3_exact_heldout": 0.80,
    "A5_p95_ms": 3000.0,
    "C1_llm_share": 0.30,
    "C2_llm_routed_exact": 0.80,
}
MUTATING = {
    "RECORD_MEASUREMENT", "RECORD_OBSERVATION", "SET_TIMER", "PAUSE_SESSION",
    "RESUME_SESSION", "COMPLETE_STEP", "END_SESSION",
}  # fmt: skip


def load_results() -> dict[str, dict[str, Any]]:
    results = {}
    for path in sorted(RESULTS.glob("*.json")):
        if path.name == "FREEZE.json":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        # Preflights (development split) and D7 (held-out A only) are partial
        # by design and are analysed by research.interpreter.analyze_d7.
        if not {"benchmark", "heldout"} <= set(data.get("results", {})):
            continue
        results[path.stem] = data
    return results


def adjudicated_unsafe(record: dict[str, Any], adjudication: dict[str, Any]) -> bool | None:
    """Recompute 'unsafe after gates' for an adjudicated benchmark item."""

    item = adjudication.get(record["id"])
    if item is None:
        return None
    executed = record["components"]["confidence"]["executed"]
    if not executed:
        return False
    return not (
        record["intent"] in MUTATING
        and semantically_equal_parameters(
            record["intent"], record["parameters"], item["expected_parameters"]
        )
    )


def adjusted_summary(
    summary: dict[str, Any], records: list[dict[str, Any]], adjudication: dict[str, Any]
) -> dict[str, Any]:
    unsafe = summary["unsafe_after_gates"]
    exact = summary["exact_correct_valid"]
    for record in records:
        if record.get("status") == "not_run_budget":
            continue
        recomputed = adjudicated_unsafe(record, adjudication)
        if recomputed is None:
            continue
        if record["components"]["confidence"]["unsafe_executed"] and not recomputed:
            unsafe -= 1
        item = adjudication[record["id"]]
        if record["intent"] in MUTATING and semantically_equal_parameters(
            record["intent"], record["parameters"], item["expected_parameters"]
        ) and not record["exact_correct"]:
            exact += 1
    valid = summary["valid_items"]
    return {"unsafe_after_gates": unsafe, "exact_accuracy_valid": exact / valid if valid else 0.0}


def decide(row: dict[str, Any], rules_unsafe: dict[str, int]) -> tuple[str, list[str]]:
    failures: list[str] = []
    if row["kind"] != "rules":
        if row["schema_validity"] < THRESHOLDS["A1_schema_validity"]:
            failures.append(f"A1 schema {row['schema_validity']:.3f}")
    if row["bench_exact_adj"] < THRESHOLDS["A2_exact_benchmark"]:
        failures.append(f"A2 benchmark exact {row['bench_exact_adj']:.3f}")
    if row["heldout_exact"] < THRESHOLDS["A3_exact_heldout"]:
        failures.append(f"A3 held-out exact {row['heldout_exact']:.3f}")
    if row["bench_unsafe_adj"] > rules_unsafe["benchmark"]:
        failures.append(f"A4 benchmark unsafe {row['bench_unsafe_adj']} > {rules_unsafe['benchmark']}")
    if row["heldout_unsafe"] > rules_unsafe["heldout"]:
        failures.append(f"A4 held-out unsafe {row['heldout_unsafe']} > {rules_unsafe['heldout']}")
    if row["p95_ms"] > THRESHOLDS["A5_p95_ms"]:
        failures.append(f"A5 p95 {row['p95_ms']:.0f} ms")
    if row["kind"] in {"cascade", "agreement"}:
        if row["heldout_llm_share"] < THRESHOLDS["C1_llm_share"]:
            failures.append(f"C1 LLM share {row['heldout_llm_share']:.2f}")
        if row["heldout_llm_exact"] < THRESHOLDS["C2_llm_routed_exact"]:
            failures.append(f"C2 LLM-routed exact {row['heldout_llm_exact']:.3f}")
    return ("ACCEPT" if not failures else "REJECT"), failures


def build_rows(results: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    adjudication = json.loads(ADJUDICATION.read_text(encoding="utf-8"))["items"]
    rows = []
    for name, data in results.items():
        config = data["config"]
        bench = data["results"]["benchmark"]
        held = data["results"]["heldout"]
        held_b = data["results"].get("heldout_b")
        adj = adjusted_summary(bench["summary"], bench["records"], adjudication)
        latencies = [bench["summary"]["latency_p95_ms"], held["summary"]["latency_p95_ms"]]
        row = {
            "configuration": name,
            "kind": config["kind"],
            "provider": config.get("provider", "deterministic"),
            "label": config.get("label", config.get("provider", "rules")),
            "model": config.get("model", "rule-grammar-v1"),
            "verifier": config.get("verifier_model", ""),
            "few_shot": config.get("few_shot", True) if config["kind"] != "rules" else "",
            "think": config.get("think", "auto") if config.get("provider") == "ollama" else "",
            "quantisation": _quantisation(data.get("model_details", "")),
            "reasoning_effort": config.get("reasoning_effort", ""),
            "post_hoc": "D6" if config.get("model") in {"gpt-5.4-nano", "gpt-5.6-luna"} else "",
            "prompt_sha256": data.get("prompt_sha256", "")[:12],
            "schema_sha256": data.get("schema_sha256", "")[:12],
            "schema_validity": min(
                bench["summary"]["schema_valid_llm"] or 1.0, held["summary"]["schema_valid_llm"] or 1.0
            ) if config["kind"] != "rules" else 1.0,
            "bench_intent": bench["summary"]["intent_accuracy_all"],
            "bench_exact_frozen": bench["summary"]["exact_accuracy_valid"],
            "bench_exact_adj": adj["exact_accuracy_valid"],
            "bench_unsafe_proposals": bench["summary"]["unsafe_proposals"],
            "bench_unsafe_frozen": bench["summary"]["unsafe_after_gates"],
            "bench_unsafe_adj": adj["unsafe_after_gates"],
            "bench_abstentions": bench["summary"]["abstentions_valid"],
            "bench_llm_routed": f"{bench['summary']['llm_routed_valid']}/{bench['summary']['valid_items']}",
            "heldout_exact": held["summary"]["exact_accuracy_valid"],
            "heldout_exact_n": f"{held['summary']['exact_correct_valid']}/{held['summary']['valid_items']}",
            "heldout_unsafe": held["summary"]["unsafe_after_gates"],
            "heldout_unsafe_proposals": held["summary"]["unsafe_proposals"],
            "heldout_abstentions": held["summary"]["abstentions_valid"],
            "heldout_llm_routed": f"{held['summary']['llm_routed_valid']}/{held['summary']['valid_items']}",
            "heldout_llm_share": held["summary"]["llm_routed_share_valid"],
            "heldout_llm_exact": held["summary"]["llm_routed_exact_accuracy"],
            "heldout_llm_unsafe": held["summary"]["llm_routed_unsafe"],
            "heldout_llm_abstentions": held["summary"]["llm_routed_abstentions"],
            "heldout_b_exact": held_b["summary"]["exact_accuracy_valid"] if held_b else "",
            "heldout_b_unsafe": held_b["summary"]["unsafe_after_gates"] if held_b else "",
            "heldout_b_llm_routed": (
                f"{held_b['summary']['llm_routed_valid']}/{held_b['summary']['valid_items']}"
                if held_b else ""
            ),
            "p50_ms": max(bench["summary"]["latency_p50_ms"], held["summary"]["latency_p50_ms"]),
            "p95_ms": max(latencies),
            "cold_ms": bench["summary"]["latency_cold_ms"],
            "peak_rss_mb": max(bench["peak_rss_mb"], held["peak_rss_mb"]),
            "vram": _vram(data.get("ollama_ps", "")),
            "hardware": data["hardware"].get("gpu", ""),
        }
        rows.append(row)
    rules = next(row for row in rows if row["kind"] == "rules")
    baseline = {"benchmark": rules["bench_unsafe_adj"], "heldout": rules["heldout_unsafe"]}
    for row in rows:
        row["decision"], failures = decide(row, baseline)
        row["reason"] = "meets A1-A5" + (" and C1-C2" if row["kind"] in {"cascade", "agreement"} else "") if not failures else "; ".join(failures)
    return rows


def _quantisation(details: str) -> str:
    try:
        return str(json.loads(details).get("quantization_level", "")) if details else ""
    except json.JSONDecodeError:
        return ""


def _vram(ollama_ps: str) -> str:
    lines = [line for line in ollama_ps.splitlines()[1:] if line.strip()]
    return " | ".join(" ".join(line.split()[2:5]) for line in lines)


def write_tables(rows: list[dict[str, Any]]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "model-selection.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    header = (
        "| Configuration | Kind | Model (verifier) | Schema | Bench exact frozen/adj | "
        "Bench unsafe frozen/adj | Held-out exact | Held-out unsafe | LLM-routed held-out | "
        "LLM-routed exact | Held-out B exact | p95 ms | Decision | Reason |\n"
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|\n"
    )
    lines = []
    for r in rows:
        model = r["model"] + (f" ({r['verifier']})" if r["verifier"] else "")
        lines.append(
            f"| {r['configuration']} | {r['kind']} | {model} | {r['schema_validity']:.3f} | "
            f"{r['bench_exact_frozen']:.3f}/{r['bench_exact_adj']:.3f} | "
            f"{r['bench_unsafe_frozen']}/{r['bench_unsafe_adj']} | "
            f"{r['heldout_exact']:.3f} ({r['heldout_exact_n']}) | {r['heldout_unsafe']} | "
            f"{r['heldout_llm_routed']} | {r['heldout_llm_exact']:.3f} | "
            f"{r['heldout_b_exact'] if r['heldout_b_exact'] == '' else format(r['heldout_b_exact'], '.3f')} | "
            f"{r['p95_ms']:.0f} | **{r['decision']}** | {r['reason']} |"
        )
    (OUT / "model-selection-table.md").write_text(
        "# Text-interpreter model selection (generated)\n\n"
        "Generated by `python -m research.interpreter.analyze_sweep` from "
        "`research/interpreter/results/*.json`. Held-out = public human-written "
        "text (CLINC150, CookDial, Wizard of Tasks); benchmark = authored. "
        "'adj' applies the D4 slot adjudication of four benchmark paraphrases.\n\n"
        + header + "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def figures(rows: list[dict[str, Any]], results: dict[str, dict[str, Any]]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    colours = {"rules": "#444444", "llm": "#1f77b4", "cascade": "#2ca02c", "agreement": "#9467bd"}
    # Risk-coverage: coverage of valid held-out items vs unsafe executions per 100 commands.
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    for axis, dataset in zip(axes, ("benchmark", "heldout"), strict=True):
        for row in rows:
            summary = results[row["configuration"]]["results"][dataset]["summary"]
            risk = 100 * (
                row["bench_unsafe_adj"] if dataset == "benchmark" else summary["unsafe_after_gates"]
            ) / max(1, summary["items"])
            axis.scatter(summary["coverage_valid"], risk, color=colours.get(row["kind"], "k"), s=36)
            axis.annotate(row["configuration"], (summary["coverage_valid"], risk), fontsize=6,
                          xytext=(3, 2), textcoords="offset points")  # fmt: skip
        axis.set_xlabel("coverage (valid items reaching the correct outcome)")
        axis.set_ylabel("unsafe executions after deterministic gates per 100 items")
        axis.set_title("Frozen benchmark (adjudicated)" if dataset == "benchmark" else "Held-out human-written")
        axis.grid(alpha=0.3)
    handles = [plt.Line2D([], [], marker="o", ls="", color=c, label=k) for k, c in colours.items()]
    axes[1].legend(handles=handles, fontsize=7, loc="upper left")
    fig.tight_layout()
    fig.savefig(OUT / "fig-risk-coverage.png", dpi=200)
    fig.savefig(OUT / "fig-risk-coverage.pdf")
    plt.close(fig)

    # Category accuracy heatmap (exact) per configuration.
    categories: list[str] = []
    matrix: list[list[float]] = []
    names = [row["configuration"] for row in rows]
    per_config: dict[str, dict[str, list[int]]] = {}
    for name in names:
        counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for dataset in ("benchmark", "heldout"):
            for record in results[name]["results"][dataset]["records"]:
                if record.get("status") == "not_run_budget":
                    continue
                key = record["category"] if dataset == "benchmark" else f"H:{record['category'].replace('heldout_', '')}"
                counts[key][0] += int(record["exact_correct"] or (not record["valid"] and record["intent"] == "UNSUPPORTED"))
                counts[key][1] += 1
        per_config[name] = counts
        for key in counts:
            if key not in categories:
                categories.append(key)
    categories.sort()
    for name in names:
        matrix.append([
            per_config[name][c][0] / per_config[name][c][1] if per_config[name].get(c, [0, 0])[1] else float("nan")
            for c in categories
        ])  # fmt: skip
    fig, axis = plt.subplots(figsize=(max(8, len(categories) * 0.55), max(4, len(names) * 0.32)))
    image = axis.imshow(matrix, cmap="viridis", vmin=0, vmax=1, aspect="auto")
    axis.set_xticks(range(len(categories)), categories, rotation=70, fontsize=7)
    axis.set_yticks(range(len(names)), names, fontsize=7)
    fig.colorbar(image, ax=axis, label="correct outcome rate")
    axis.set_title("Per-category correctness (benchmark categories; H: = held-out)")
    fig.tight_layout()
    fig.savefig(OUT / "fig-category-accuracy.png", dpi=200)
    fig.savefig(OUT / "fig-category-accuracy.pdf")
    plt.close(fig)

    # Latency.
    fig, axis = plt.subplots(figsize=(9, max(3.5, len(names) * 0.3)))
    for index, row in enumerate(rows):
        axis.barh(index, row["p95_ms"], color=colours.get(row["kind"], "k"), alpha=0.5)
        axis.plot(row["p50_ms"], index, "k|", markersize=10)
    axis.axvline(THRESHOLDS["A5_p95_ms"], color="red", ls="--", lw=1, label="A5: p95 = 3 s")
    axis.set_yticks(range(len(names)), names, fontsize=7)
    axis.set_xlabel("warm latency, ms (bar = p95, tick = p50)")
    axis.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(OUT / "fig-latency.png", dpi=200)
    fig.savefig(OUT / "fig-latency.pdf")
    plt.close(fig)


def main() -> int:
    results = load_results()
    rows = build_rows(results)
    write_tables(rows)
    figures(rows, results)
    for row in rows:
        print(f"{row['decision']:6} {row['configuration']:45} {row['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
