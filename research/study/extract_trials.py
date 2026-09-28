"""Extract anonymised confirmation trials from study-session notebook exports.

Input: the JSON exports (``/api/v1/sessions/{id}/export?format=json``) of
study-mode sessions. Each measurement proposal carries its study provenance on
its text inference (``study_participant``, ``study_condition``, ``study_trial``,
``study_injected``). Output: one CSV row per trial with the decision and the
confirmation time - no transcript, audio or personal data.

    python -m research.study.extract_trials <export.json>... --out trials.csv
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

FIELDS = (
    "study_id",
    "participant",
    "condition",
    "block",
    "trial",
    "injected",
    "intent",
    "decision",
    "confirmation_ms",
    "source",
)


def _parse(moment: str | None) -> datetime | None:
    return datetime.fromisoformat(moment) if moment else None


def trials_from_export(document: dict[str, Any]) -> list[dict[str, Any]]:
    inferences = {item["id"]: item for item in document.get("inferences", [])}
    rows: list[dict[str, Any]] = []
    for action in document.get("pending_actions", []):
        inference = inferences.get(action.get("model_inference_id") or "")
        output = inference["output"] if inference else {}
        if "study_trial" not in output:
            continue
        created = _parse(action.get("created_at"))
        resolved = _parse(action.get("resolved_at"))
        status = action.get("status", "")
        rows.append(
            {
                "study_id": output.get("study_id", ""),
                "participant": output.get("study_participant", ""),
                "condition": output.get("study_condition", ""),
                "block": output.get("study_block", ""),
                "trial": int(output["study_trial"]),
                "injected": output.get("study_injected") == "true",
                "intent": action.get("intent", ""),
                "decision": {"CONFIRMED": "accepted", "REJECTED": "rejected"}.get(status, status.lower()),
                "confirmation_ms": (
                    round((resolved - created).total_seconds() * 1000)
                    if created is not None and resolved is not None
                    else ""
                ),
                "source": action.get("source", ""),
            }
        )
    return sorted(rows, key=lambda row: (row["participant"], row["block"], row["trial"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exports", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    rows: list[dict[str, Any]] = []
    for path in args.exports:
        rows.extend(trials_from_export(json.loads(path.read_text(encoding="utf-8"))))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)} trials -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
