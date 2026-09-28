"""Evaluate deterministic grounding across independently published protocols."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from labjarvis.domain.grounding import retrieve
from labjarvis.domain.models import (
    ProtocolStepDraft,
    ProtocolVersion,
    ProtocolVersionStatus,
    SourceReference,
)


def _version(source: dict[str, Any]) -> ProtocolVersion:
    steps = tuple(
        ProtocolStepDraft(
            id=step["id"],
            number=step["number"],
            title=step["title"],
            instruction=step["instruction"],
            requirements=(),
            duration_seconds=step["duration_seconds"],
            safety_notes=(),
            source=SourceReference(
                page=step["number"],
                section=step["title"],
                excerpt=step["instruction"],
            ),
        )
        for step in source["steps"]
    )
    created = datetime(2026, 9, 1, tzinfo=UTC)
    return ProtocolVersion(
        id=source["id"],
        protocol_id=source["id"],
        version_number=1,
        revision=1,
        status=ProtocolVersionStatus.APPROVED,
        steps=steps,
        source_sha256=source["content_sha256"],
        created_at=created,
        source_text=source["source_text"],
        approved_at=created,
    )


def evaluate_grounding_cases(sources_path: Path, cases_path: Path) -> dict[str, Any]:
    sources = json.loads(sources_path.read_text(encoding="utf-8"))
    versions = {source["id"]: _version(source) for source in sources}
    cases = [
        json.loads(line)
        for line in cases_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    grounding = [case for case in cases if case["kind"] == "grounding"]
    records: list[dict[str, Any]] = []
    for case in grounding:
        result = retrieve(versions[case["source_id"]], case["question"], limit=1)
        top = result.passages[0] if result.passages else None
        expected_supported = bool(case["expected_supported"])
        passage_correct = bool(
            top
            and str(case["expected_contains"]).casefold() in top.text.casefold()
        )
        correct = (not result.supported) if not expected_supported else passage_correct
        records.append(
            {
                "case_id": case["id"],
                "source_id": case["source_id"],
                "expected_supported": expected_supported,
                "supported": result.supported,
                "correct": correct,
                "top_chunk_id": top.chunk_id if top else None,
                "score": top.score if top else None,
            }
        )
    supported_records = [record for record in records if record["expected_supported"]]
    abstention_records = [record for record in records if not record["expected_supported"]]
    return {
        "evidence_class": "public_protocol_source_independent_not_human_adjudicated",
        "source_count": len(sources),
        "case_count": len(records),
        "supported_accuracy": sum(record["correct"] for record in supported_records)
        / max(1, len(supported_records)),
        "abstention_accuracy": sum(record["correct"] for record in abstention_records)
        / max(1, len(abstention_records)),
        "records": records,
    }


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    public = root / "research" / "post_review" / "public_protocols"
    result = evaluate_grounding_cases(public / "sources.json", public / "cases.jsonl")
    output = root / "research" / "post_review" / "results" / "public-protocol-grounding.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
