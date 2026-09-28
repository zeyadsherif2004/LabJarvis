"""Live "scan until stable" vs the three-frame capture - PROVISIONAL, SYNTHETIC.

Evidence class: SYNTHETIC (rendered labels, simulated hand-held frames). No
paid calls; local EasyOCR only. This is a stand-in until real image sequences or
a webcam run exist; real bursts can be replayed with the same code.

For each of the 90 rendered labels a stream is simulated: two heavily blurred
"bottle still moving into view" frames, then seeded small perturbations
(rotation +/-4 degrees, brightness 0.85-1.15, shift up to 6 px, as in
``multiframe_ablation``). Policies, on the same frames:

* ``three_frame`` - production countdown capture: the first three steady
  frames (after the countdown), ``unanimous-v1``;
* ``live_scan`` - ``stable-asymmetric-v3`` (3 matching outcomes, or 3 identical
  non-matching texts, among the most recent 5 probes; 400 ms, 12 s -> max 30
  probes) over the whole stream, including the blurred lead-in; on stability
  the selected agreeing frames go through ``unanimous-v1`` (as in production).

Reported per policy: false accepts (a mismatch/decoy read as MATCH), correct
mismatches, uncertain/timeouts, coverage of true matches, and for live scan the
frames and simulated time to stability.

    python -m research.vision.live_scan_simulation [--gpu]
"""

from __future__ import annotations

import argparse
import io
import json
import random
from collections import Counter
from statistics import median
from time import perf_counter
from typing import Any

from PIL import Image, ImageFilter

from labjarvis.domain.live_scan import (
    RULE_VERSION,
    UNREADABLE,
    ProbeReading,
    ScanRule,
    ScanStatus,
    evaluate_scan,
)
from labjarvis.domain.models import VerificationOutcome
from labjarvis.domain.reagent import classify_reagent
from labjarvis.domain.reagent_consensus import FrameReading, unanimous_consensus
from labjarvis.infrastructure.real_models import EasyOcrLabelRecognition
from research.vision.multiframe_ablation import (
    MANIFEST,
    MATCH_T,
    OUT,
    ROOT,
    UNCERTAIN_T,
    outcome_class,
    perturb,
)

SEED = 20260928
RULE = ScanRule(required_consecutive=3, interval_ms=400, timeout_ms=12_000)
LEAD_IN_BLURRED = 2


def blurred(image: Image.Image, radius: float) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").filter(ImageFilter.GaussianBlur(radius)).save(buffer, format="PNG")
    return buffer.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpu", action="store_true")
    args = parser.parse_args()
    rows = [json.loads(line) for line in MANIFEST.read_text(encoding="utf-8").splitlines() if line.strip()]
    ocr = EasyOcrLabelRecognition(
        languages=("en",), gpu=args.gpu, cache_dir=ROOT / ".labjarvis" / "models" / "easyocr"
    )
    rng = random.Random(SEED)
    records: list[dict[str, Any]] = []
    ocr_ms: list[float] = []

    def read(frame: bytes, expected: str) -> FrameReading:
        started = perf_counter()
        text = ocr.recognise(frame, content_type="image/png").visible_text
        ocr_ms.append((perf_counter() - started) * 1000)
        return FrameReading(
            visible_text=text,
            comparison=classify_reagent(
                text, expected, match_threshold=MATCH_T, uncertain_threshold=UNCERTAIN_T
            ),
        )

    for row in rows:
        base = Image.open((MANIFEST.parent / row["media_path"]).resolve())
        expected = row["expected_reagent"]
        stream = [blurred(base, 6 + 3 * i) for i in range(LEAD_IN_BLURRED)]
        stream += [perturb(base, rng) for _ in range(RULE.max_frames - LEAD_IN_BLURRED)]
        cache: dict[int, FrameReading] = {}

        def reading_at(index: int) -> FrameReading:
            if index not in cache:
                cache[index] = read(stream[index], expected)
            return cache[index]

        # three-frame countdown: the operator holds still after the countdown,
        # so it sees the first three steady frames.
        steady = [reading_at(LEAD_IN_BLURRED + i) for i in range(3)]
        three = unanimous_consensus(steady).outcome

        probes: list[ProbeReading] = []
        state = None
        for index in range(len(stream)):
            frame = reading_at(index)
            readable = bool(frame.comparison.observed_normalised.strip())
            probes.append(
                ProbeReading(
                    frame.comparison.outcome.value if readable else UNREADABLE,
                    frame.comparison.observed_normalised,
                )
            )
            state = evaluate_scan(probes, elapsed_ms=(index + 1) * RULE.interval_ms, rule=RULE)
            if state.status is not ScanStatus.SCANNING:
                break
        assert state is not None
        if state.status is ScanStatus.STABLE:
            selected = [cache[i] for i in state.frame_indexes]
            live = unanimous_consensus(selected).outcome.value
        else:
            live = "TIMEOUT"
        records.append(
            {
                "id": row["id"],
                "truth": row["truth"],
                "variant": row["variant"],
                "expected": expected,
                "printed": row["printed_text"],
                "three_frame": three.value,
                "live_scan": live,
                "live_status": state.status.value,
                "live_reason": state.reason,
                "probe_readings": [[probe.outcome, probe.normalised] for probe in probes],
                "frames_examined": state.frames_examined,
                "simulated_ms_to_decision": state.frames_examined * RULE.interval_ms,
            }
        )

    def classify(truth: str, outcome: str) -> str:
        if outcome == "TIMEOUT":
            return "timeout"
        return outcome_class(truth, VerificationOutcome(outcome))

    summary: dict[str, Any] = {}
    for policy in ("three_frame", "live_scan"):
        counts = Counter(classify(r["truth"], r[policy]) for r in records)
        matches = [r for r in records if r["truth"] == "match"]
        summary[policy] = {
            "n": len(records),
            "counts": dict(sorted(counts.items())),
            "false_accepts": counts.get("false_accept", 0),
            "coverage_of_true_matches": round(
                sum(classify(r["truth"], r[policy]) == "correct" for r in matches) / max(1, len(matches)), 3
            ),
            "correct_mismatch_or_decoy": sum(
                classify(r["truth"], r[policy]) == "correct" and r["truth"] != "match" for r in records
            ),
        }
    stable = [r for r in records if r["live_status"] == "stable"]
    summary["live_scan"]["stable"] = len(stable)
    summary["live_scan"]["timeouts"] = dict(Counter(r["live_reason"] for r in records if r["live_status"] == "timeout"))
    if stable:
        frames = sorted(r["frames_examined"] for r in stable)
        summary["live_scan"]["frames_to_stability_median"] = median(frames)
        summary["live_scan"]["frames_to_stability_p90"] = frames[round(0.9 * (len(frames) - 1))]
        summary["live_scan"]["simulated_seconds_to_stability_median"] = median(frames) * RULE.interval_ms / 1000
    ordered = sorted(ocr_ms[1:])
    summary["ocr_ms_per_probe_p50"] = round(ordered[len(ordered) // 2], 1)
    summary["ocr_ms_per_probe_p95"] = round(ordered[round(0.95 * (len(ordered) - 1))], 1)
    changed = [r for r in records if classify(r["truth"], r["three_frame"]) != classify(r["truth"], r["live_scan"])]
    payload = {
        "evidence_class": "PROVISIONAL SYNTHETIC: rendered labels, simulated hand-held frames with a blurred lead-in; local EasyOCR",
        "rule": {"version": RULE_VERSION, "required_consecutive": RULE.required_consecutive,
                 "interval_ms": RULE.interval_ms, "timeout_ms": RULE.timeout_ms,
                 "max_frames": RULE.max_frames, "lead_in_blurred_frames": LEAD_IN_BLURRED},
        "gpu": args.gpu,
        "summary": summary,
        "items_whose_outcome_class_differs": [
            {k: r[k] for k in ("id", "truth", "variant", "printed", "three_frame", "live_scan")} for r in changed
        ],
        "records": records,
    }  # fmt: skip
    OUT.mkdir(parents=True, exist_ok=True)
    # Keep the v1 and v2 results intact; their numbers belong to earlier rules.
    (OUT / "live-scan-simulation-v3.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    print("differing items:", len(changed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
