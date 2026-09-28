"""The empty real-media templates carry every field the evaluation code reads."""

import json
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent / "templates"


def _rows(name: str) -> list[dict]:
    return [json.loads(line) for line in (TEMPLATES / name).read_text(encoding="utf-8").splitlines() if line]


def test_speech_template_has_the_chain_runner_fields() -> None:
    for row in _rows("speech-manifest.template.jsonl"):
        assert {"id", "media_path", "content_type", "expected_text", "expected_intent",
                "expected_parameters", "speaker", "condition", "split", "evidence_class"} <= set(row)
        assert row["speaker"].startswith("P") and "private" in row["media_path"]


def test_label_template_has_the_chain_runner_fields() -> None:
    for row in _rows("label-manifest.template.jsonl"):
        assert {"id", "media_path", "frame_paths", "expected_reagent", "expected_outcome",
                "truth", "class", "condition", "split"} <= set(row)
        assert row["expected_outcome"] in {"MATCH", "MISMATCH", "UNCERTAIN"}
