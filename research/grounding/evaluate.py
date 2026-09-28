"""Evaluate grounded protocol Q&A generalisation (development vs held-out test).

The development split is the three September protocols (already seen and used
to motivate v2); the test split is four independently authored OpenWetWare
protocols fetched at pinned revisions. The v2 floor is chosen on development
only (balanced accuracy, ties to the higher, safer floor); the test split is
reported at that floor and across every floor as a risk-coverage curve.

    python -m research.grounding.evaluate
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from labjarvis.domain.grounding import (  # noqa: E402
    RELEVANCE_FLOOR,
    RELEVANCE_FLOOR_V2,
    GroundedRetrieval,
    retrieve_v1,
    retrieve_v2,
)
from labjarvis.domain.models import ProtocolVersion  # noqa: E402
from research.post_review.evaluate_protocol_breadth import _version  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
DEV_SOURCES = ROOT / "research" / "post_review" / "public_protocols" / "sources.json"
TEST_SOURCES = HERE / "protocols-test.json"
CASES = HERE / "cases.jsonl"
OUT = ROOT / "research" / "final_report_evidence" / "grounding"
FLOORS = [round(0.05 * step, 2) for step in range(1, 17)]

Retriever = Callable[..., GroundedRetrieval]


def versions() -> dict[str, ProtocolVersion]:
    sources = json.loads(DEV_SOURCES.read_text(encoding="utf-8")) + json.loads(
        TEST_SOURCES.read_text(encoding="utf-8")
    )
    return {source["id"]: _version(source) for source in sources}


def cases() -> list[dict[str, Any]]:
    return [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line]


def score(retriever: Retriever, floor: float, split: str) -> dict[str, Any]:
    corpus = versions()
    selected = [case for case in cases() if case["split"] == split]
    records = []
    for case in selected:
        result = retriever(corpus[case["source_id"]], case["question"], limit=1, floor=floor)
        top = result.passages[0] if result.passages else None
        if case["expected_supported"]:
            correct = bool(
                top and case["expected_contains"].casefold() in top.text.casefold()
            )
        else:
            correct = not result.supported
        records.append(
            {
                "id": case["id"],
                "question": case["question"],
                "expected_supported": case["expected_supported"],
                "answered": result.supported,
                "correct": correct,
                "score": top.score if top else None,
                "excerpt": top.text[:160] if top else "",
            }
        )
    supported = [r for r in records if r["expected_supported"]]
    unsupported = [r for r in records if not r["expected_supported"]]
    recall = sum(r["correct"] for r in supported) / len(supported)
    unsupported_answered = sum(r["answered"] for r in unsupported) / len(unsupported)
    return {
        "floor": floor,
        "n": len(records),
        "supported_n": len(supported),
        "unsupported_n": len(unsupported),
        "answer_recall": round(recall, 4),
        "unsupported_answer_rate": round(unsupported_answered, 4),
        "false_abstentions": sum((not r["answered"]) for r in supported),
        "wrong_passage": sum(r["answered"] and not r["correct"] for r in supported),
        "balanced_accuracy": round((recall + (1 - unsupported_answered)) / 2, 4),
        "records": records,
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    methods: dict[str, tuple[Retriever, float]] = {
        "v1": (retrieve_v1, RELEVANCE_FLOOR),
        "v2": (retrieve_v2, RELEVANCE_FLOOR_V2),
    }
    report: dict[str, Any] = {"floors": FLOORS, "methods": {}}
    for name, (retriever, production_floor) in methods.items():
        dev_curve = [score(retriever, floor, "dev") for floor in FLOORS]
        best = max(dev_curve, key=lambda row: (row["balanced_accuracy"], row["floor"]))
        test_curve = [score(retriever, floor, "test") for floor in FLOORS]
        report["methods"][name] = {
            "production_floor": production_floor,
            "dev_selected_floor": best["floor"],
            "dev_at_production_floor": score(retriever, production_floor, "dev"),
            "test_at_production_floor": score(retriever, production_floor, "test"),
            "dev_curve": [{k: v for k, v in row.items() if k != "records"} for row in dev_curve],
            "test_curve": [{k: v for k, v in row.items() if k != "records"} for row in test_curve],
        }
    (OUT / "grounding-results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    for axis, split in zip(axes, ("dev", "test"), strict=True):
        for name, style in (("v1", "o--"), ("v2", "s-")):
            curve = report["methods"][name][f"{split}_curve"]
            axis.plot(
                [row["unsupported_answer_rate"] for row in curve],
                [row["answer_recall"] for row in curve],
                style,
                label=name,
            )
            chosen = report["methods"][name][f"{split}_at_production_floor"]
            axis.scatter(chosen["unsupported_answer_rate"], chosen["answer_recall"], s=90,
                         facecolors="none", edgecolors="red")  # fmt: skip
        axis.set_xlabel("unsupported questions answered (rate)")
        axis.set_ylabel("supported questions answered correctly (recall)")
        axis.set_title(f"{'Development' if split == 'dev' else 'Held-out test'} split")
        axis.set_xlim(-0.05, 1.05)
        axis.set_ylim(-0.05, 1.05)
        axis.grid(alpha=0.3)
        axis.legend(title="red circle = production floor")
    fig.tight_layout()
    fig.savefig(OUT / "fig-grounding-risk-coverage.png", dpi=200)
    fig.savefig(OUT / "fig-grounding-risk-coverage.pdf")
    plt.close(fig)

    for name, method in report["methods"].items():
        for split in ("dev", "test"):
            row = method[f"{split}_at_production_floor"]
            print(
                f"{name} {split} floor {row['floor']}: recall {row['answer_recall']:.2f} "
                f"({row['supported_n']}), unsupported answered {row['unsupported_answer_rate']:.2f} "
                f"({row['unsupported_n']}), balanced {row['balanced_accuracy']:.2f}"
            )
        print(f"{name} dev-selected floor: {method['dev_selected_floor']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
