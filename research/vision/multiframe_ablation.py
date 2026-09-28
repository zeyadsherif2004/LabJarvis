"""Single-frame vs multi-frame consensus for reagent-label verification.

Evidence class: SYNTHETIC. Each of the 90 rendered labels is the first frame
of a simulated hand-held burst; the other frames are small, seeded
perturbations (rotation +/-4 degrees, brightness 0.85-1.15, shift up to 6 px)
that imitate a label held in front of a webcam. The same frames feed every
policy, so the comparison is paired. Real photographs, once collected, are run
through the same code with ``--frames-from`` pointing at genuine bursts.

Policies:
* ``single`` - production comparator on frame 1 only;
* ``unanimous-3`` - production ``unanimous-v1`` (any disagreement -> UNCERTAIN);
* ``majority-3`` - research ablation (strict majority, ties -> UNCERTAIN).

    python -m research.vision.multiframe_ablation [--gpu]
"""

from __future__ import annotations

import argparse
import io
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from time import perf_counter
from typing import Any

from PIL import Image, ImageEnhance

from labjarvis.domain.models import VerificationOutcome
from labjarvis.domain.reagent import classify_reagent
from labjarvis.domain.reagent_consensus import FrameReading, majority_consensus, unanimous_consensus
from labjarvis.infrastructure.real_models import EasyOcrLabelRecognition

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "research" / "data" / "manifests" / "label-images.jsonl"
OUT = ROOT / "research" / "final_report_evidence" / "hands-free"
SEED = 20260927
MATCH_T, UNCERTAIN_T = 92.0, 72.0


def perturb(image: Image.Image, rng: random.Random) -> bytes:
    frame = image.convert("RGB").rotate(rng.uniform(-4, 4), expand=False, fillcolor=(255, 255, 255))
    frame = ImageEnhance.Brightness(frame).enhance(rng.uniform(0.85, 1.15))
    dx, dy = rng.randint(-6, 6), rng.randint(-6, 6)
    frame = frame.transform(frame.size, Image.Transform.AFFINE, (1, 0, dx, 0, 1, dy), fillcolor=(255, 255, 255))
    buffer = io.BytesIO()
    frame.save(buffer, format="PNG")
    return buffer.getvalue()


def outcome_class(truth: str, outcome: VerificationOutcome) -> str:
    if outcome is VerificationOutcome.UNCERTAIN:
        return "uncertain"
    if truth == "match":
        return "correct" if outcome is VerificationOutcome.MATCH else "false_reject"
    if outcome is VerificationOutcome.MATCH:
        return "false_accept"
    return "correct"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpu", action="store_true")
    parser.add_argument("--frames", type=int, default=3)
    args = parser.parse_args()
    rows = [json.loads(line) for line in MANIFEST.read_text(encoding="utf-8").splitlines() if line.strip()]
    ocr = EasyOcrLabelRecognition(
        languages=("en",), gpu=args.gpu, cache_dir=ROOT / ".labjarvis" / "models" / "easyocr"
    )
    rng = random.Random(SEED)
    records: list[dict[str, Any]] = []
    for row in rows:
        path = (MANIFEST.parent / row["media_path"]).resolve()
        original = path.read_bytes()
        base = Image.open(io.BytesIO(original))
        frames = [original] + [perturb(base, rng) for _ in range(args.frames - 1)]
        readings, times = [], []
        for frame in frames:
            started = perf_counter()
            text = ocr.recognise(frame, content_type="image/png").visible_text
            times.append((perf_counter() - started) * 1000)
            readings.append(
                FrameReading(
                    visible_text=text,
                    comparison=classify_reagent(
                        text, row["expected_reagent"], match_threshold=MATCH_T, uncertain_threshold=UNCERTAIN_T
                    ),
                )
            )
        single = readings[0].comparison.outcome
        unanimous = unanimous_consensus(readings).outcome
        majority = majority_consensus(readings)
        records.append(
            {
                "id": row["id"],
                "truth": row["truth"],
                "variant": row["variant"],
                "printed": row["printed_text"],
                "expected": row["expected_reagent"],
                "frame_texts": [r.visible_text for r in readings],
                "single": single.value,
                "unanimous": unanimous.value,
                "majority": majority.value,
                "ocr_ms": [round(t, 1) for t in times],
            }
        )
    summary: dict[str, Any] = {}
    for policy in ("single", "unanimous", "majority"):
        counts: Counter[str] = Counter()
        by_truth: dict[str, Counter[str]] = defaultdict(Counter)
        for record in records:
            label = outcome_class(record["truth"], VerificationOutcome(record[policy]))
            counts[label] += 1
            by_truth[record["truth"]][label] += 1
        frames_used = 1 if policy == "single" else args.frames
        latency = sorted(sum(r["ocr_ms"][:frames_used]) for r in records[1:])
        summary[policy] = {
            "n": len(records),
            "counts": dict(counts),
            "by_truth": {k: dict(v) for k, v in by_truth.items()},
            "false_accepts": counts["false_accept"],
            "false_rejects": counts["false_reject"],
            "uncertain": counts["uncertain"],
            "coverage_of_true_matches": by_truth["match"]["correct"] / max(1, sum(by_truth["match"].values())),
            "ocr_ms_p50": latency[len(latency) // 2],
            "ocr_ms_p95": latency[min(len(latency) - 1, round(0.95 * (len(latency) - 1)))],
        }
    payload = {
        "evidence_class": "synthetic (rendered labels; simulated burst perturbations)",
        "seed": SEED,
        "frames": args.frames,
        "device": "gpu" if args.gpu else "cpu",
        "policies": summary,
        "records": records,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "multiframe-ablation.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    lines = [
        "| Policy | False accepts | False rejects | UNCERTAIN | True-match coverage | OCR p50 / p95 (ms) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for policy, stats in summary.items():
        lines.append(
            f"| {policy} | {stats['false_accepts']} | {stats['false_rejects']} | {stats['uncertain']} | "
            f"{stats['coverage_of_true_matches']:.2f} | {stats['ocr_ms_p50']:.0f} / {stats['ocr_ms_p95']:.0f} |"
        )
    (OUT / "multiframe-ablation.md").write_text(
        "# Multi-frame consensus ablation (synthetic, generated)\n\n" + "\n".join(lines) + "\n", encoding="utf-8"
    )
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
