"""Grammar v2 (hands-free) against the frozen benchmark and held-out sets. No models.

For every utterance: does version 1 still produce exactly the same result, and
what do the new hands-free rules newly match? Newly matched utterances are
compared with their labels; the point is to show that no v1 result changes and
that nothing newly matched is a state change the label did not ask for.

    python -m research.speech.grammar_compatibility
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from labjarvis.infrastructure.rule_grammar import GRAMMAR_V1, GRAMMAR_VERSION, parse_rule_command

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "research" / "final_report_evidence" / "hands-free" / "grammar-v2-compatibility.json"
SETS = {
    "benchmark (synthetic authored, 81)": "research/data/manifests/text-commands.jsonl",
    "held-out A (public human-written)": "research/data/heldout/human-paraphrases.jsonl",
    "held-out B (public human-written)": "research/data/heldout/human-paraphrases-B.jsonl",
}
READ_ONLY = {
    "ANSWER_PROTOCOL_QUESTION", "HELP", "GET_NEXT_STEP", "GET_CURRENT_STEP", "GET_ACTIVE_TIMERS",
    "GET_EXPECTED_REAGENTS", "GET_PROTOCOL_STATE", "READ_LAST_ENTRY", "VERIFY_REAGENT",
}  # fmt: skip


def main() -> int:
    report: dict[str, object] = {
        "created_at": datetime.now(UTC).isoformat(),
        "grammar": {"old": GRAMMAR_V1, "new": GRAMMAR_VERSION},
        "evidence_class": "benchmark: synthetic authored; held-out: public human-written text",
        "sets": {},
    }
    for name, relative in SETS.items():
        rows = [
            json.loads(line)
            for line in (ROOT / relative).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        changed, newly = 0, []
        for row in rows:
            text = row.get("transcript") or row.get("text") or row.get("utterance") or ""
            v1 = parse_rule_command(text, hands_free=False)
            v2 = parse_rule_command(text)
            if v1.matched and v1.command != v2.command:
                changed += 1
            if not v1.matched and v2.matched:
                newly.append(
                    {"text": text, "v2_intent": v2.command.intent, "label": row.get("expected_intent")}
                )
        state_changing_unlabelled = [
            item for item in newly
            if item["v2_intent"] not in READ_ONLY and item["v2_intent"] != item["label"]
        ]  # fmt: skip
        report["sets"][name] = {  # type: ignore[index]
            "utterances": len(rows),
            "v1_results_changed": changed,
            "newly_matched": len(newly),
            "newly_matched_agreeing_with_label": sum(i["v2_intent"] == i["label"] for i in newly),
            "newly_matched_by_intent": dict(Counter(i["v2_intent"] for i in newly)),
            "newly_matched_state_changes_not_in_label": len(state_changing_unlabelled),
            "newly_matched_items": newly,
        }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=1), encoding="utf-8")
    for name, summary in report["sets"].items():  # type: ignore[union-attr]
        print(name, {k: v for k, v in summary.items() if k != "newly_matched_items"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
