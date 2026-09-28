"""Regenerate the report-ready figures, tables and hashed index from raw results.

Inputs are the raw JSON results already in this package (never edited by
hand). Outputs:

* ``defence-in-depth/`` - errors surviving each layer (model output -> schema
  -> deterministic validation -> state -> derived confidence / agreement ->
  human confirmation -> execution) for the key text configurations;
* ``latency/`` - per-stage latency waterfall for the real local chain, CPU vs
  GPU;
* ``INDEX.md`` / ``index.json`` - every evidence file with its SHA-256 and
  evidence class.

    python -m research.final_report_evidence.build_evidence
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RESULTS = ROOT / "research" / "interpreter" / "results"
KEY_CONFIGS = (
    "rules",
    "qwen2.5-3b-llm",
    "qwen2.5-3b-cascade",
    "qwen2.5-1.5b-cascade-verified-qwen2.5-3b",
    "deepseek-flash-cascade",
)
GATES = (
    ("proposal", "model output"),
    ("validation", "+ schema & deterministic validation"),
    ("state", "+ state gate"),
    ("confidence", "+ derived confidence / agreement"),
    ("confirm_attentive", "+ attentive human confirmation"),
)
#: Files whose evidence class differs from their folder's.
FILE_CLASSES = {
    "live/d8-vision.json": "PROVISIONAL: live hosted vision on synthetic rendered labels",
    "live/d8-chain.json": "live hosted + local models on synthetic speech and rendered labels",
    "model-selection/d7-luna-vs-terra.json": "public human-written text (held-out A), live hosted",
    "hands-free/hands-free-evaluation.json": (
        "real local models on synthetic inputs (2 TTS voices; Whisper small.en + base.en, "
        "grammar v2 + qwen2.5:3b, EasyOCR on rendered labels)"
    ),
    "hands-free/voice-only-confirmation.json": (
        "real local models on synthetic inputs (2 TTS voices; Whisper small.en + base.en)"
    ),
    "hands-free/zero-touch-e2e.json": "fake/fixture (deterministic FAKE: speech, Playwright)",
    "hands-free/hands-free-evaluation.md": "summary by evidence class (fake; real local models on synthetic inputs)",
    "hands-free/grammar-v2-compatibility.json": (
        "benchmark: synthetic authored; held-out: public human-written text (grammar only)"
    ),
    "verification/property-1000.json": "fake/fixture (Hypothesis stateful, fake adapters)",
}
CLASSES = {
    "model-selection": "benchmark: synthetic authored; held-out: public human-written text",
    "live": "live hosted (paid, capped ledger)",
    "real-chain": "real local models; synthetic TTS speech and rendered labels",
    "real-world": (
        "real world: one operator (the developer), own voice, microphone, webcam and "
        "handwritten label; hosted speech; build changed during the session"
    ),
    "devices": "real device latency; synthetic inputs",
    "hands-free": "synthetic",
    "speech": "synthetic TTS speech",
    "grounding": "public protocol text (CC BY-SA/CC BY) + authored questions",
    "verification": "deterministic software verification",
}


def defence_in_depth() -> None:
    out = HERE / "defence-in-depth"
    out.mkdir(exist_ok=True)
    rows: list[dict[str, Any]] = []
    for name in KEY_CONFIGS:
        path = RESULTS / f"{name}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for dataset in ("benchmark", "heldout"):
            gates = data["results"][dataset]["summary"]["gates"]
            rows.append(
                {
                    "configuration": name,
                    "dataset": dataset,
                    **{gate: gates[gate]["unsafe_executed"] for gate, _ in GATES},
                    "always_accept": gates["confirm_always_accept"]["unsafe_executed"],
                }
            )
    (out / "defence-in-depth.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    lines = [
        "| Configuration | Set | Model output | + validation | + state | + confidence/agreement | + attentive confirmation | if always accepted |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['configuration']} | {row['dataset']} | {row['proposal']} | {row['validation']} | "
            f"{row['state']} | {row['confidence']} | {row['confirm_attentive']} | {row['always_accept']} |"
        )
    (out / "defence-in-depth.md").write_text(
        "# Unsafe commands surviving each layer (generated; frozen benchmark labels)\n\n"
        + "\n".join(lines)
        + "\n\nCounts are unsafe *executions* if every gate up to that column applied. "
        "'Attentive confirmation' assumes the operator rejects every wrong proposal - an "
        "assumption the user study is designed to test; 'always accepted' is the worst case.\n",
        encoding="utf-8",
    )
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), sharey=True)
    for axis, dataset in zip(axes, ("benchmark", "heldout"), strict=True):
        for row in (r for r in rows if r["dataset"] == dataset):
            axis.plot(range(len(GATES)), [row[g] for g, _ in GATES], marker="o", label=row["configuration"])
        axis.set_xticks(range(len(GATES)), [label for _, label in GATES], rotation=25, ha="right", fontsize=7)
        axis.set_title("Frozen benchmark (81)" if dataset == "benchmark" else "Held-out human-written (138)")
        axis.grid(alpha=0.3)
    axes[0].set_ylabel("unsafe executions remaining")
    axes[1].legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(out / "fig-defence-in-depth.png", dpi=200)
    fig.savefig(out / "fig-defence-in-depth.pdf")
    plt.close(fig)


def latency_waterfall() -> None:
    out = HERE / "latency"
    out.mkdir(exist_ok=True)
    chain = json.loads((HERE / "real-chain" / "chain-demo.json").read_text(encoding="utf-8"))
    devices = json.loads((HERE / "devices" / "device-benchmark.json").read_text(encoding="utf-8"))
    del chain  # the chain demo proves the path; warm latencies come from repeated runs
    cascade = json.loads((RESULTS / "qwen2.5-3b-cascade.json").read_text(encoding="utf-8"))
    llm_text_ms = cascade["results"]["heldout"]["summary"]["latency_p95_ms"]
    ocr_cpu = devices["results"]["easyocr_cpu"]["warm_p95_ms"]
    ocr_gpu = devices["results"]["easyocr_gpu"]["warm_p95_ms"]
    stages = {
        "CPU": [("speech (Whisper small.en)", devices["results"]["whisper_cpu"]["warm_p95_ms"]),
                ("text (grammar route)", 1.0), ("text (LLM route, qwen2.5:3b)", llm_text_ms),
                ("label OCR x3 frames", 3 * ocr_cpu)],
        "GPU": [("speech (Whisper small.en)", devices["results"]["whisper_gpu"]["warm_p95_ms"]),
                ("text (grammar route)", 1.0), ("text (LLM route, qwen2.5:3b)", llm_text_ms),
                ("label OCR x3 frames", 3 * ocr_gpu)],
    }  # fmt: skip
    (out / "latency-waterfall.json").write_text(json.dumps(stages, indent=2), encoding="utf-8")
    fig, axis = plt.subplots(figsize=(9, 3.2))
    for row, (profile, parts) in enumerate(stages.items()):
        left = 0.0
        for index, (label, ms) in enumerate(parts):
            axis.barh(row, ms, left=left, color=f"C{index}", label=label if row == 0 else None)
            left += ms
        axis.text(left + 50, row, f"{left / 1000:.2f} s", va="center", fontsize=8)
    axis.set_yticks(range(len(stages)), list(stages))
    axis.set_xlabel("warm p95 per stage, ms (one spoken command + one 3-frame label check)")
    axis.legend(fontsize=7, loc="lower right")
    fig.tight_layout()
    fig.savefig(out / "fig-latency-waterfall.png", dpi=200)
    fig.savefig(out / "fig-latency-waterfall.pdf")
    plt.close(fig)


def index() -> None:
    entries = []
    for path in sorted(HERE.rglob("*")):
        if path.is_dir() or path.suffix in {".py", ".pyc"} or path.name in {"INDEX.md", "index.json"}:
            continue
        relative = path.relative_to(HERE).as_posix()
        entries.append(
            {
                "file": relative,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "evidence_class": FILE_CLASSES.get(
                    relative, CLASSES.get(relative.split("/")[0], "see file")
                ),
            }
        )
    (HERE / "index.json").write_text(json.dumps(entries, indent=2), encoding="utf-8")
    lines = ["# Final-report evidence index (generated)", "", "| File | Evidence class | SHA-256 |", "|---|---|---|"]
    lines += [f"| `{e['file']}` | {e['evidence_class']} | `{e['sha256'][:16]}...` |" for e in entries]
    (HERE / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    defence_in_depth()
    latency_waterfall()
    index()
    print((HERE / "defence-in-depth" / "defence-in-depth.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
