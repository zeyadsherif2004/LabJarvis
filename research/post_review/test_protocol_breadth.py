import json
from pathlib import Path

from research.post_review.evaluate_protocol_breadth import evaluate_grounding_cases


ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "research" / "post_review" / "public_protocols"


def test_public_sources_are_diverse_open_and_frozen_before_execution() -> None:
    sources = json.loads((PUBLIC / "sources.json").read_text(encoding="utf-8"))

    assert len(sources) >= 3
    assert len({source["doi"] for source in sources}) == len(sources)
    assert len({tuple(source["authors"]) for source in sources}) == len(sources)
    assert all(source["license"] == "CC BY 4.0" for source in sources)
    assert all(source["retrieved_at"] == "2026-09-01" for source in sources)
    assert all(len(source["content_sha256"]) == 64 for source in sources)


def test_public_cases_cover_every_source_and_include_abstentions() -> None:
    cases = [
        json.loads(line)
        for line in (PUBLIC / "cases.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    sources = json.loads((PUBLIC / "sources.json").read_text(encoding="utf-8"))

    assert {case["source_id"] for case in cases} == {source["id"] for source in sources}
    grounding = [case for case in cases if case["kind"] == "grounding"]
    commands = [case for case in cases if case["kind"] == "command"]
    assert len(grounding) >= 9
    assert len(commands) >= 9
    assert any(case["expected_supported"] is False for case in grounding)


def test_grounding_evaluator_reports_supported_accuracy_and_abstention_accuracy() -> None:
    result = evaluate_grounding_cases(PUBLIC / "sources.json", PUBLIC / "cases.jsonl")

    assert result["source_count"] >= 3
    assert result["case_count"] >= 9
    assert 0 <= result["supported_accuracy"] <= 1
    assert 0 <= result["abstention_accuracy"] <= 1
    assert len(result["records"]) == result["case_count"]
