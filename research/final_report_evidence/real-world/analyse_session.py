"""Derive the real-world session metrics from a LabJarvis session export.

Everything reported in ``session-2026-09-28-saline.md`` comes from this script,
run over the exported notebook (schema ``labjarvis.session`` 2.0). Nothing is
typed in by hand, so the numbers can be regenerated and checked.

    python research/final_report_evidence/real-world/analyse_session.py
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
EXPORT = HERE / "session-2026-09-28-saline.json"
OUT = HERE / "session-2026-09-28-metrics.json"
#: A pause longer than this between notebook events is treated as time away
#: from the bench (troubleshooting, restarts), not as time spent on the task.
IDLE_GAP_SECONDS = 600


def _t(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _conflict_cause(primary: str, verification: str, reasons: list[str]) -> str:
    """Classify why the two transcriptions disagreed, from their text alone."""

    if any(ord(character) > 0x2FF for character in verification + primary):
        return "non-Latin script or diacritic in one transcription"
    lowered = f"{primary} {verification}".casefold()
    if any(word in lowered for word in ("komplet", "kompliet", "komplitt", "rekord", "conferm", "gradi")):
        return "phonetic or non-English spelling in one transcription"
    number_words = ("one", "two", "three", "four", "five", "twenty", "thirty")
    if any(word in primary.casefold() for word in number_words) and any(c.isdigit() for c in verification):
        return "number written as words in one transcription and digits in the other"
    if reasons and "value differs" in reasons[0]:
        return "number written as words in one transcription and digits in the other"
    return "different words heard"


def analyse(data: dict[str, Any]) -> dict[str, Any]:
    session = data["session"]
    version = data["protocol"]["version"]
    entries = sorted(data["notebook_entries"], key=lambda e: e["created_at"])
    inferences = {i["id"]: i for i in data["inferences"]}
    utterances = sorted(data["utterances"], key=lambda u: u["created_at"])
    actions = sorted(data["pending_actions"], key=lambda a: a["created_at"])

    ended = _t(session["ended_at"])
    gaps = []
    for before, after in zip(entries, entries[1:], strict=False):
        gap = (_t(after["created_at"]) - _t(before["created_at"])).total_seconds()
        if gap > IDLE_GAP_SECONDS:
            gaps.append(
                {"from": before["created_at"], "to": after["created_at"], "minutes": round(gap / 60, 1)}
            )
    wall = (ended - _t(session["created_at"])).total_seconds()
    idle = sum(gap["minutes"] * 60 for gap in gaps)

    steps = []
    for draft, run in zip(version["steps"], session["steps"], strict=True):
        steps.append(
            {
                "number": draft["number"],
                "instruction": draft["instruction"],
                "state": run["state"],
                "minutes_in_progress": round(
                    (_t(run["completed_at"]) - _t(run["started_at"])).total_seconds() / 60, 1
                ),
            }
        )

    # Utterances: what was said, and how the two transcriptions compared.
    conflict_causes: Counter[str] = Counter()
    conflicts = []
    for utterance in utterances:
        if utterance["agreement"] != "conflict":
            continue
        primary = (inferences.get(utterance["primary_inference_id"]) or {}).get("output", {})
        verification = (inferences.get(utterance["verification_inference_id"]) or {}).get("output", {})
        primary_text = primary.get("transcript", "") or ""
        verification_text = verification.get("transcript", "") or ""
        cause = _conflict_cause(primary_text, verification_text, utterance["reasons"])
        conflict_causes[cause] += 1
        conflicts.append(
            {
                "at": utterance["created_at"],
                "purpose": utterance["purpose"],
                "primary": primary_text,
                "verification": verification_text,
                "cause": cause,
            }
        )

    verification_models = Counter(
        (u["purpose"], u["verification_model"]) for u in utterances
    )

    # Proposals and how they ended.
    proposals = Counter((a["intent"], a["status"]) for a in actions)
    measurement = [a for a in actions if a["intent"] == "RECORD_MEASUREMENT"]
    measurement_attempts = [
        u
        for u in utterances
        if u["purpose"] == "confirmation" and u["pending_action_id"] in {a["id"] for a in measurement}
    ]
    first_measurement = measurement[0]["created_at"] if measurement else None
    committed = [a for a in measurement if a["status"] == "ACCEPTED"]

    guidance = Counter(
        e["payload"].get("intent") for e in entries if e["entry_type"] == "grounded_guidance"
    )
    questions = [
        {"question": e["payload"].get("transcript"), "answer": e["payload"].get("response_text")}
        for e in entries
        if e["entry_type"] == "grounded_guidance"
        and e["payload"].get("intent") == "ANSWER_PROTOCOL_QUESTION"
    ]
    refused = [
        {"said": e["payload"].get("transcript"), "response": e["payload"].get("response_text")}
        for e in entries
        if e["entry_type"] == "command_rejected"
    ]

    verifications = [
        {
            "at": r["created_at"],
            "observed": r["observed_label"],
            "outcome": r["outcome"],
            "score": r["score"],
            "threshold": r["match_threshold"],
            "decision": r["decision"],
            "reader": next(
                (
                    i["model_name"]
                    for i in data["inferences"]
                    if i["modality"] == "vision" and i["id"] == r["model_inference_id"]
                ),
                None,
            ),
        }
        for r in sorted(data["reagent_verifications"], key=lambda r: r["created_at"])
    ]
    proposal_entries = {
        e["payload"].get("verification_id") or e["id"]: e["payload"]
        for e in entries
        if e["entry_type"] == "reagent_verification_proposal"
    }
    scan_rules = sorted(
        {p.get("capture_scan_rule") for p in proposal_entries.values() if p.get("capture_scan_rule")}
    )
    capture_modes = Counter(p.get("capture_mode") for p in proposal_entries.values())

    latency: dict[str, dict[str, float]] = {}
    groups: dict[str, list[int]] = {}
    for inference in data["inferences"]:
        if inference["status"] != "succeeded":
            continue
        key = f"{inference['modality']} / {inference['model_name']}"
        if inference["modality"] == "text":
            key += " / llm" if inference["output"].get("llm_called") == "true" else " / rule"
        groups.setdefault(key, []).append(inference["latency_ms"])
    for key, values in sorted(groups.items()):
        latency[key] = {
            "n": len(values),
            "median_ms": statistics.median(values),
            "max_ms": max(values),
        }
    failures = Counter(
        (i["modality"], i["output"].get("failure_code", "unknown"))
        for i in data["inferences"]
        if i["status"] != "succeeded"
    )

    timers = [
        {
            "seconds": t["duration_seconds"],
            "state": t["state"],
            "started": t["started_at"],
            "deadline": t["deadline_at"],
        }
        for t in data["timers"]
    ]
    audit = sorted(data["audit_events"], key=lambda a: a["created_at"])
    elapsed = next((a for a in audit if a["event_type"] == "timer_elapsed"), None)
    silenced = next((a for a in audit if a["event_type"] == "timer_alarm_acknowledged"), None)

    committed_entries = [
        {"type": e["entry_type"], "payload": {k: v for k, v in e["payload"].items() if k in {"kind", "value", "unit", "observation", "old_value"}}}
        for e in entries
        if e["entry_type"] in {"measurement", "correction", "observation", "timer"}
    ]

    return {
        "source_export": EXPORT.name,
        "export_schema": f"{data['schema_name']} {data['schema_version']}",
        "exported_at": data["exported_at"],
        "application_commit": data["application_commit"],
        "protocol": {
            "title": data["protocol"]["title"],
            "approved_at": version["approved_at"],
            "review_revisions": version["revision"],
            "steps": len(version["steps"]),
        },
        "session": {
            "state": session["state"],
            "created_at": session["created_at"],
            "started_at": session["started_at"],
            "ended_at": session["ended_at"],
            "wall_clock_minutes": round(wall / 60, 1),
            "idle_gaps_over_10_min": gaps,
            "minutes_excluding_idle_gaps": round((wall - idle) / 60, 1),
        },
        "steps": steps,
        "notebook_entry_types": dict(Counter(e["entry_type"] for e in entries)),
        "utterances": {
            "total": len(utterances),
            "by_purpose": dict(Counter(u["purpose"] for u in utterances)),
            "agreement": dict(Counter(u["agreement"] for u in utterances)),
            "confirmation_decisions": dict(
                Counter(u["decision"] for u in utterances if u["purpose"] == "confirmation")
            ),
            "verification_models": {f"{p} / {m}": n for (p, m), n in verification_models.items()},
            "conflict_causes": dict(conflict_causes),
            "conflicts": conflicts,
        },
        "proposals": {f"{intent} {status}": n for (intent, status), n in sorted(proposals.items())},
        "measurement_confirmation": {
            "proposals": len(measurement),
            "spoken_confirmation_attempts": len(measurement_attempts),
            "attempt_decisions": dict(Counter(u["decision"] for u in measurement_attempts)),
            "first_proposed_at": first_measurement,
            "committed_at": committed[0]["resolved_at"] if committed else None,
            "minutes_from_first_proposal_to_commit": (
                round((_t(committed[0]["resolved_at"]) - _t(first_measurement)).total_seconds() / 60, 1)
                if committed and first_measurement
                else None
            ),
        },
        "guidance_by_intent": dict(guidance),
        "protocol_questions": questions,
        "refused_commands": refused,
        "reagent_verifications": verifications,
        "reagent_capture": {"modes": dict(capture_modes), "live_scan_rules_seen": scan_rules},
        "timers": timers,
        "timer_alarm": {
            "elapsed_at": elapsed["created_at"] if elapsed else None,
            "silenced_at": silenced["created_at"] if silenced else None,
            "seconds_to_silence": (
                round((_t(silenced["created_at"]) - _t(elapsed["created_at"])).total_seconds(), 1)
                if elapsed and silenced
                else None
            ),
        },
        "committed_records": committed_entries,
        "inference_latency": latency,
        "inference_failures": {f"{m} {c}": n for (m, c), n in failures.items()},
        "inference_total": len(data["inferences"]),
    }


def main() -> int:
    data = json.loads(EXPORT.read_text(encoding="utf-8"))
    result = analyse(data)
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("session", "utterances", "measurement_confirmation")}, indent=2, ensure_ascii=False)[:6000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
