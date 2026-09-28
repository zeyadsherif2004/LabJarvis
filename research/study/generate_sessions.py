"""Generate counterbalanced study session scripts and printable session sheets.

Within-subject comparison of two confirmation designs: ``tap`` (on-screen
confirm) and ``readback`` (say the value back). Each participant does one block
per condition; the order alternates (AB, BA, AB, ...) so order effects cancel
across participants. Each block has ``--trials`` measurement dictations of
which ``--seeded`` (2-3) are injected with a plausible wrong value, at
positions drawn with a fixed seed and never on the first trial. One label
check per block may carry a wrong reagent.

Outputs (never participant data - only the plan):

* ``<out>/P01-block1-tap.json`` ... study scripts for LABJARVIS_STUDY_MODE_SCRIPT;
* ``<out>/session-sheets.md`` - the facilitator's trial list per participant;
* ``<out>/plan.json`` - the full allocation with its seed.

    python -m research.study.generate_sessions --participants 8 --out <private dir>
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

#: Trial utterances: plausible, safe desk-simulation measurements with a
#: nearby wrong value an attentive operator should catch.
TRIALS: tuple[tuple[str, str, str, str], ...] = (
    ("record the volume as 5 mL", "5", "50", "mL"),
    ("record the temperature as 37 degrees Celsius", "37", "27", "degC"),
    ("record the volume as 250 microlitres", "250", "25", "uL"),
    ("record the mass as 1.5 grams", "1.5", "15", "g"),
    ("record the ph as 7.4 ph", "7.4", "4.7", "pH"),
    ("record the speed as 3000 rpm", "3000", "300", "rpm"),
    ("record the temperature as 4 degrees Celsius", "4", "14", "degC"),
    ("record the volume as 12.5 mL", "12.5", "125", "mL"),
    ("record the mass as 200 milligrams", "200", "20", "mg"),
    ("record the concentration as 70 percent", "70", "7", "%"),
    ("record the volume as 2 litres", "2", "20", "L"),
    ("record the temperature as 21.5 degrees Celsius", "21.5", "12.5", "degC"),
)
WRONG_REAGENTS = ("METHANOL", "ACETONE", "ISOPROPANOL")


def plan(participants: int, *, trials: int, seeded: int, seed: int, study_id: str) -> dict[str, Any]:
    if not 2 <= seeded <= 3:
        raise ValueError("use 2-3 seeded errors per condition")
    if trials > len(TRIALS):
        raise ValueError(f"at most {len(TRIALS)} trials per block")
    rng = random.Random(seed)
    allocation = []
    for number in range(1, participants + 1):
        order = ("tap", "readback") if number % 2 == 1 else ("readback", "tap")
        blocks = []
        for block, condition in enumerate(order, 1):
            items = list(TRIALS[:trials])
            rng.shuffle(items)
            positions = sorted(rng.sample(range(2, trials + 1), seeded))
            label_trial_wrong = rng.random() < 0.5
            injections = [
                {
                    "trial": position,
                    "kind": "measurement_value",
                    "to": items[position - 1][2],
                    "note": f"{items[position - 1][1]} -> {items[position - 1][2]} "
                    f"{items[position - 1][3]}",
                }
                for position in positions
            ]
            if label_trial_wrong:
                injections.append(
                    {"trial": 1, "kind": "reagent_label", "to": rng.choice(WRONG_REAGENTS)}
                )
            blocks.append(
                {
                    "study_id": study_id,
                    "participant": f"P{number:02d}",
                    "condition": condition,
                    "block": block,
                    "injections": injections,
                    "trial_utterances": [item[0] for item in items],
                }
            )
        allocation.append({"participant": f"P{number:02d}", "order": list(order), "blocks": blocks})
    return {"study_id": study_id, "seed": seed, "trials_per_block": trials, "allocation": allocation}


def write(plan_data: dict[str, Any], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "plan.json").write_text(json.dumps(plan_data, indent=2), encoding="utf-8")
    sheets = [f"# Session sheets - {plan_data['study_id']} (seed {plan_data['seed']})\n"]
    sheets.append(
        "Facilitator only. Participants are told before starting that **some proposals "
        "will deliberately be wrong** and that they should reject anything that does "
        "not match what they said.\n"
    )
    for person in plan_data["allocation"]:
        sheets.append(f"\n## {person['participant']} - order {' then '.join(person['order'])}\n")
        for block in person["blocks"]:
            script = {k: v for k, v in block.items() if k != "trial_utterances"}
            name = f"{block['participant']}-block{block['block']}-{block['condition']}.json"
            (out / name).write_text(json.dumps(script, indent=2), encoding="utf-8")
            sheets.append(f"\n### Block {block['block']}: {block['condition']} (`{name}`)\n")
            seeded = {i["trial"] for i in block["injections"] if i["kind"] == "measurement_value"}
            for index, utterance in enumerate(block["trial_utterances"], 1):
                mark = "  <- seeded error" if index in seeded else ""
                sheets.append(f"{index}. \"{utterance}\"{mark}")
            sheets.append("\nThen: \"check reagent\" with the printed label (label trial 1).")
    (out / "session-sheets.md").write_text("\n".join(sheets) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--participants", type=int, default=8)
    parser.add_argument("--trials", type=int, default=10)
    parser.add_argument("--seeded", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260926)
    parser.add_argument("--study-id", default="labjarvis-desk-study")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    write(
        plan(args.participants, trials=args.trials, seeded=args.seeded, seed=args.seed,
             study_id=args.study_id),
        args.out,
    )  # fmt: skip
    print(f"wrote study plan to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
