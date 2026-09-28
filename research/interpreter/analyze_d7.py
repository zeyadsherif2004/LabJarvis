"""D7 analysis: gpt-5.6-luna vs gpt-5.6-terra on held-out A, with repeatability.

Written before any D7 result existed (see research/evidence/protocol-deviations.md,
D7). Inputs, all model-alone on held-out A with reasoning effort ``none``:

* Luna run 1 = the held-out A part of D6 ``openai-gpt-5.6-luna-llm``;
* Luna run 2 = ``d7-gpt-5.6-luna-A-run2``;
* Terra run 1 / run 2 = ``d7-gpt-5.6-terra-A-run1`` / ``-run2``.

For each run: supported items covered, exact count, unsafe executions after the
gates, schema validity, p50/p95 latency. For each model with two runs:
**consistency** = share of items covered by both runs whose canonical command
(intent + parameters) is identical, and the list of items whose safety outcome
(unsafe execution yes/no) changed. Per the predeclared rule, a Luna-Terra
difference of fewer than 5 items (exact or unsafe) is reported as no evidence
of a difference. Descriptive only: no default changes.

    python -m research.interpreter.analyze_d7
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "research" / "interpreter" / "results"
OUT = ROOT / "research" / "final_report_evidence" / "model-selection"
RUNS = {
    ("gpt-5.6-luna", 1): ("openai-gpt-5.6-luna-llm", "heldout"),
    ("gpt-5.6-luna", 2): ("d7-gpt-5.6-luna-A-run2", "heldout"),
    ("gpt-5.6-terra", 1): ("d7-gpt-5.6-terra-A-run1", "heldout"),
    ("gpt-5.6-terra", 2): ("d7-gpt-5.6-terra-A-run2", "heldout"),
}
NO_EVIDENCE_BELOW = 5
FULL_SET_ITEMS = 138  # held-out A


def _load(name: str, dataset: str) -> dict[str, Any] | None:
    path = RESULTS / f"{name}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["results"].get(dataset)


def _canonical(record: dict[str, Any]) -> str:
    return json.dumps([record.get("intent"), record.get("parameters", {})], sort_keys=True)


def _unsafe(record: dict[str, Any]) -> bool:
    return bool(record["components"]["confidence"]["unsafe_executed"])


def run_summary(result: dict[str, Any]) -> dict[str, Any]:
    done = [r for r in result["records"] if r.get("status") != "not_run_budget"]
    valid = [r for r in done if r["valid"]]
    summary = result["summary"]
    return {
        "items_run": len(done),
        "items_not_run_budget": len(result["records"]) - len(done),
        # A budget-driven prefix is INCOMPLETE even when every item it ran succeeded.
        "complete": len(done) == FULL_SET_ITEMS,
        "label": "complete" if len(done) == FULL_SET_ITEMS else f"INCOMPLETE ({len(done)}/{FULL_SET_ITEMS} items, file-order prefix)",
        "supported_items": len(valid),
        "exact": sum(r["exact_correct"] for r in valid),
        "unsafe_executions": sum(_unsafe(r) for r in done),
        "schema_valid_llm": summary["schema_valid_llm"],
        "failures": summary["failures"],
        "p50_ms": summary["latency_p50_ms"],
        "p95_ms": summary["latency_p95_ms"],
    }


def consistency(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    a = {r["id"]: r for r in first["records"] if r.get("status") != "not_run_budget"}
    b = {r["id"]: r for r in second["records"] if r.get("status") != "not_run_budget"}
    shared = sorted(set(a) & set(b))
    same = [i for i in shared if _canonical(a[i]) == _canonical(b[i])]
    safety_changed = [
        {"id": i, "run1_unsafe": _unsafe(a[i]), "run2_unsafe": _unsafe(b[i])}
        for i in shared
        if _unsafe(a[i]) != _unsafe(b[i])
    ]
    return {
        "items_in_both_runs": len(shared),
        "identical_command": len(same),
        "identical_share": round(len(same) / len(shared), 4) if shared else None,
        "safety_outcome_changed": safety_changed,
    }


def main() -> int:
    runs: dict[str, dict[str, Any]] = {}
    raw: dict[tuple[str, int], dict[str, Any]] = {}
    for (model, number), (name, dataset) in RUNS.items():
        result = _load(name, dataset)
        if result is None:
            continue
        raw[(model, number)] = result
        runs[f"{model} run {number}"] = {"source": name, **run_summary(result)}
    comparison: dict[str, Any] = {}
    for model in ("gpt-5.6-luna", "gpt-5.6-terra"):
        if (model, 1) in raw and (model, 2) in raw:
            comparison[f"{model} repeatability"] = consistency(raw[(model, 1)], raw[(model, 2)])
    if ("gpt-5.6-luna", 1) in raw and ("gpt-5.6-terra", 1) in raw:
        luna, terra = runs["gpt-5.6-luna run 1"], runs["gpt-5.6-terra run 1"]
        exact_gap = terra["exact"] - luna["exact"]
        unsafe_gap = terra["unsafe_executions"] - luna["unsafe_executions"]
        comparison["terra_minus_luna_run1"] = {
            "exact": exact_gap,
            "unsafe_executions": unsafe_gap,
            "same_items": luna["items_run"] == terra["items_run"],
            "reading": (
                "no evidence of a difference (fewer than 5 items)"
                if abs(exact_gap) < NO_EVIDENCE_BELOW and abs(unsafe_gap) < NO_EVIDENCE_BELOW
                else "difference of at least 5 items on one measure"
            ),
        }
    payload = {
        "deviation": "D7 (post-hoc, recorded before running)",
        "evidence_class": "public human-written text (held-out A), live hosted models",
        "reasoning_effort": "none (both models)",
        "runs": runs,
        "comparison": comparison,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "d7-luna-vs-terra.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(json.dumps(payload, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
