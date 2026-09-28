"""Evaluate the deterministic safety properties added by the hybrid architecture.

This measures three things that ordinary accuracy metrics do not capture:

1. **Transcript conflict handling.** Does the system stop when two readings of one
   utterance disagree about something that could change the record, and does it
   proceed when the difference could not?
2. **Grounded answering and abstention.** Does it answer what the protocol says,
   and refuse what the protocol does not say?
3. **Tool policy.** Does every action that writes to the experimental record
   require a human, and is the realtime surface a strict subset?

Everything here is deterministic and offline: no model runs, nothing is billed,
and re-running produces identical numbers. That also bounds what the results
mean. These are **engineering** measurements against **synthetic** fixtures the
author wrote. They are not a user study, and they are not evidence about how any
model behaves on real laboratory speech.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from labjarvis.application.tools import (
    TOOLS,
    ConfirmationPolicy,
    ToolKind,
    realtime_tool_schemas,
)
from labjarvis.domain.grounding import retrieve
from labjarvis.domain.models import (
    ProtocolRequirement,
    ProtocolStepDraft,
    ProtocolVersion,
    ProtocolVersionStatus,
    SourceReference,
)
from labjarvis.domain.transcripts import TranscriptAgreement, compare_transcripts

#: Tools that write to the experimental record. Each must always need a human.
RECORD_WRITING_TOOLS = frozenset(
    {
        "record_measurement",
        "record_observation",
        "complete_step",
        "skip_step_with_reason",
        "verify_reagent",
        "end_session",
    }
)


@dataclass(frozen=True, slots=True)
class ConfusionCounts:
    """Outcome counts for one binary decision."""

    true_positive: int = 0
    false_positive: int = 0
    true_negative: int = 0
    false_negative: int = 0

    @property
    def total(self) -> int:
        return (
            self.true_positive + self.false_positive + self.true_negative + self.false_negative
        )

    @property
    def accuracy(self) -> float:
        if not self.total:
            return 0.0
        return round((self.true_positive + self.true_negative) / self.total, 4)

    @property
    def precision(self) -> float:
        denominator = self.true_positive + self.false_positive
        return round(self.true_positive / denominator, 4) if denominator else 0.0

    @property
    def recall(self) -> float:
        denominator = self.true_positive + self.false_negative
        return round(self.true_positive / denominator, 4) if denominator else 0.0


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


# ------------------------------------------------------------ transcript conflicts


def evaluate_transcript_conflicts(dataset: Path) -> dict[str, Any]:
    """Measure how well material disagreements are separated from cosmetic ones.

    A **false negative** here is the dangerous outcome: a genuine number or unit
    disagreement that was let through as equivalent, which would put a wrong
    value in front of the operator as if two readers had agreed on it. A false
    positive merely asks them to repeat themselves.
    """

    rows = _read_jsonl(dataset)
    tp = fp = tn = fn = 0
    failures: list[dict[str, str]] = []
    for row in rows:
        expected_conflict = row["expected"] == "conflict"
        result = compare_transcripts(row["realtime"], row["high_accuracy"])
        detected_conflict = result.agreement is TranscriptAgreement.CONFLICT
        if expected_conflict and detected_conflict:
            tp += 1
        elif expected_conflict and not detected_conflict:
            fn += 1
            failures.append(
                {
                    "sample_id": row["sample_id"],
                    "kind": "missed_conflict",
                    "category": row["category"],
                }
            )
        elif not expected_conflict and detected_conflict:
            fp += 1
            failures.append(
                {
                    "sample_id": row["sample_id"],
                    "kind": "spurious_conflict",
                    "category": row["category"],
                }
            )
        else:
            tn += 1

        # A conflict must never resolve to a winning transcript.
        if detected_conflict and result.canonical is not None:
            failures.append(
                {
                    "sample_id": row["sample_id"],
                    "kind": "conflict_resolved_a_winner",
                    "category": row["category"],
                }
            )

    counts = ConfusionCounts(
        true_positive=tp, false_positive=fp, true_negative=tn, false_negative=fn
    )
    return {
        "sample_count": len(rows),
        "counts": asdict(counts),
        "conflict_detection_accuracy": counts.accuracy,
        "conflict_precision": counts.precision,
        "conflict_recall": counts.recall,
        # The headline safety number: proportion of genuine material
        # disagreements that were let through.
        "missed_material_conflict_rate": round(fn / (tp + fn), 4) if (tp + fn) else 0.0,
        "spurious_conflict_rate": round(fp / (fp + tn), 4) if (fp + tn) else 0.0,
        "failures": failures,
    }


# ------------------------------------------------------------------- grounding


def _evaluation_protocol() -> ProtocolVersion:
    """The fixture protocol the grounding questions are written against."""

    source = "\n".join(
        [
            "Ethanol precipitation protocol (synthetic, for evaluation only)",
            "Store all ethanol stocks below four degrees Celsius before use.",
            "1. Add 10 mL ethanol to the demonstration vessel.",
            "2. Centrifuge the vessel for 15 minutes.",
            "3. Decant the supernatant carefully and retain the pellet.",
        ]
    )
    steps = (
        ProtocolStepDraft(
            id="step-1",
            number=1,
            title="Add ethanol",
            instruction="Add 10 mL ethanol to the demonstration vessel.",
            requirements=(
                ProtocolRequirement(
                    requirement_id="step-1-req-1",
                    name="ethanol",
                    quantity=Decimal("10"),
                    unit="mL",
                ),
            ),
            duration_seconds=None,
            safety_notes=("Warning: ethanol is flammable. Keep away from ignition sources.",),
            source=SourceReference(
                page=1,
                section="Preparation",
                excerpt="1. Add 10 mL ethanol to the demonstration vessel.",
            ),
        ),
        ProtocolStepDraft(
            id="step-2",
            number=2,
            title="Centrifuge",
            instruction="Centrifuge the vessel for 15 minutes.",
            requirements=(),
            duration_seconds=900,
            safety_notes=(),
            source=SourceReference(
                page=1,
                section="Separation",
                excerpt="2. Centrifuge the vessel for 15 minutes.",
            ),
        ),
        ProtocolStepDraft(
            id="step-3",
            number=3,
            title="Decant",
            instruction="Decant the supernatant carefully and retain the pellet.",
            requirements=(),
            duration_seconds=None,
            safety_notes=(),
            source=SourceReference(
                page=2,
                section="Recovery",
                excerpt="3. Decant the supernatant carefully and retain the pellet.",
            ),
        ),
    )
    now = datetime.now(tz=UTC)
    return ProtocolVersion(
        id="evaluation-version-1",
        protocol_id="evaluation-protocol-1",
        version_number=1,
        revision=2,
        status=ProtocolVersionStatus.APPROVED,
        steps=steps,
        source_sha256="0" * 64,
        created_at=now,
        source_text=source,
        approved_at=now,
    )


def evaluate_grounding(dataset: Path) -> dict[str, Any]:
    """Measure answer accuracy and, separately, abstention accuracy.

    The number that matters most is **unsupported_answer_rate**: how often a
    question the protocol does not address received an answer anyway. In a
    laboratory that is the failure mode with real consequences, and it is the one
    a headline accuracy figure would hide.
    """

    rows = _read_jsonl(dataset)
    version = _evaluation_protocol()
    answerable_correct = answerable_total = 0
    abstain_correct = abstain_total = 0
    citation_present = 0
    wrong_step = 0
    failures: list[dict[str, str]] = []

    for row in rows:
        result = retrieve(version, row["question"])
        if row["expected"] == "answerable":
            answerable_total += 1
            if result.supported:
                answerable_correct += 1
                best = result.passages[0]
                if best.citation:
                    citation_present += 1
                expected_step = row.get("expected_step_id")
                if expected_step is not None and best.step_id != expected_step:
                    wrong_step += 1
                    failures.append(
                        {
                            "sample_id": row["sample_id"],
                            "kind": "wrong_passage",
                            "expected_step_id": str(expected_step),
                            "actual_step_id": str(best.step_id),
                        }
                    )
            else:
                failures.append({"sample_id": row["sample_id"], "kind": "over_abstained"})
        else:
            abstain_total += 1
            if result.supported:
                failures.append(
                    {
                        "sample_id": row["sample_id"],
                        "kind": "answered_unsupported_question",
                        "returned": result.passages[0].text[:80],
                    }
                )
            else:
                abstain_correct += 1

    return {
        "sample_count": len(rows),
        "answerable_count": answerable_total,
        "abstain_count": abstain_total,
        "answer_recall": (
            round(answerable_correct / answerable_total, 4) if answerable_total else 0.0
        ),
        "abstention_accuracy": round(abstain_correct / abstain_total, 4) if abstain_total else 0.0,
        # Answering a question the document does not address.
        "unsupported_answer_rate": (
            round((abstain_total - abstain_correct) / abstain_total, 4) if abstain_total else 0.0
        ),
        "citation_coverage": (
            round(citation_present / answerable_correct, 4) if answerable_correct else 0.0
        ),
        "wrong_passage_count": wrong_step,
        "failures": failures,
    }


# ---------------------------------------------------------------- tool policy


def evaluate_tool_policy() -> dict[str, Any]:
    """Check the registry's invariants directly, as measurable properties."""

    violations: list[dict[str, str]] = []
    for name in sorted(RECORD_WRITING_TOOLS):
        spec = TOOLS.get(name)
        if spec is None:
            violations.append({"tool": name, "issue": "missing_from_registry"})
            continue
        if spec.confirmation is not ConfirmationPolicy.ALWAYS:
            violations.append(
                {"tool": name, "issue": "record_write_without_mandatory_confirmation"}
            )

    for spec in TOOLS.values():
        if spec.kind is ToolKind.READ_ONLY and spec.confirmation is not ConfirmationPolicy.NONE:
            violations.append({"tool": spec.name, "issue": "read_only_requires_confirmation"})
        if spec.kind is ToolKind.MUTATING and spec.confirmation is ConfirmationPolicy.NONE:
            violations.append({"tool": spec.name, "issue": "mutation_without_policy"})

    exposed = {schema["name"] for schema in realtime_tool_schemas()}
    if not exposed < set(TOOLS):
        violations.append({"tool": "*", "issue": "realtime_surface_is_not_a_strict_subset"})

    return {
        "tool_count": len(TOOLS),
        "realtime_exposed_count": len(exposed),
        "record_writing_tools_always_confirmed": not any(
            item["issue"] == "record_write_without_mandatory_confirmation" for item in violations
        ),
        "realtime_surface_is_strict_subset": exposed < set(TOOLS),
        "violations": violations,
    }


# ------------------------------------------------------------------- reporting


def collect_failures(report: Mapping[str, Any]) -> tuple[dict[str, str], ...]:
    """Every case that did not hold, flattened for a single verdict.

    Kept separate from reporting so the exit status and the printed summary can
    never disagree about how many failures there were - which is exactly what
    went wrong before, when the summary printed one failure and the process
    still exited successfully.
    """

    failures: list[dict[str, str]] = []
    for item in report["transcript_conflicts"]["failures"]:
        failures.append({"suite": "transcript_conflicts", **{k: str(v) for k, v in item.items()}})
    for item in report["grounding"]["failures"]:
        failures.append({"suite": "grounding", **{k: str(v) for k, v in item.items()}})
    for item in report["tool_policy"]["violations"]:
        failures.append({"suite": "tool_policy", **{k: str(v) for k, v in item.items()}})
    return tuple(failures)


def run(dataset_dir: Path, output: Path) -> Mapping[str, Any]:
    """Run every safety evaluation and write one JSON report."""

    report: dict[str, Any] = {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "evidence_class": "synthetic-deterministic",
        "caveat": (
            "Deterministic engineering measurements against synthetic fixtures "
            "written by the author. Not a user study, and not evidence about any "
            "model's behaviour on real laboratory speech."
        ),
        "transcript_conflicts": evaluate_transcript_conflicts(
            dataset_dir / "transcript-conflicts.jsonl"
        ),
        "grounding": evaluate_grounding(dataset_dir / "grounding-questions.jsonl"),
        "tool_policy": evaluate_tool_policy(),
    }
    failures = collect_failures(report)
    # The verdict travels with the report so a CI job, the evidence register,
    # and a human reading the file all see the same answer.
    report["failures"] = [dict(item) for item in failures]
    report["failure_count"] = len(failures)
    report["passed"] = not failures
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def _summarise(report: Mapping[str, Any]) -> Iterable[str]:
    conflicts = report["transcript_conflicts"]
    grounding = report["grounding"]
    policy = report["tool_policy"]
    yield "Transcript conflict handling"
    yield f"  samples                        {conflicts['sample_count']}"
    yield f"  conflict detection accuracy    {conflicts['conflict_detection_accuracy']}"
    yield f"  missed material conflict rate  {conflicts['missed_material_conflict_rate']}"
    yield f"  spurious conflict rate         {conflicts['spurious_conflict_rate']}"
    yield "Grounded answering"
    yield f"  samples                        {grounding['sample_count']}"
    yield f"  answer recall                  {grounding['answer_recall']}"
    yield f"  abstention accuracy            {grounding['abstention_accuracy']}"
    yield f"  unsupported answer rate        {grounding['unsupported_answer_rate']}"
    yield f"  citation coverage              {grounding['citation_coverage']}"
    yield "Tool policy"
    yield f"  tools                          {policy['tool_count']}"
    yield f"  realtime exposed               {policy['realtime_exposed_count']}"
    yield f"  record writes always confirmed {policy['record_writing_tools_always_confirmed']}"
    yield f"  violations                     {len(policy['violations'])}"


def main() -> int:
    """Run the suite and return a status that reflects the result.

    This used to print the failure count and then return zero, which made it
    useless as a quality gate: a regression in confirmation policy or in
    grounding would have been reported and then ignored by anything checking the
    exit status. A failing case now fails the command.
    """

    root = Path(__file__).resolve().parents[5]
    output = root / "evaluation" / "results" / "safety-properties.json"
    report = run(dataset_dir=root / "evaluation" / "datasets", output=output)
    for line in _summarise(report):
        print(line)

    failures = collect_failures(report)
    print(f"\ntotal failures: {len(failures)}")
    for failure in failures:
        detail = ", ".join(f"{key}={value}" for key, value in sorted(failure.items()))
        print(f"  FAIL {detail}")
    print(f"report written to {output}")
    if failures:
        print("\nSafety evaluation FAILED.", file=sys.stderr)
        return 1
    print("\nSafety evaluation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
