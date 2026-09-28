"""The synthetic evaluation must submit the frames selected by the scan rule."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from labjarvis.domain.live_scan import ScanRule
from research.vision import live_scan_simulation as simulation


def test_simulation_verifies_agreeing_frames_after_one_ocr_misread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stray MISMATCH must not be among the selected MATCH frames."""

    image_path = tmp_path / "label.png"
    Image.new("RGB", (8, 8), "white").save(image_path)
    manifest = tmp_path / "labels.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "id": "handheld-salt",
                "truth": "match",
                "variant": "handheld",
                "expected_reagent": "sodium chloride",
                "printed_text": "SODIUM CHLORIDE",
                "media_path": image_path.name,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    frame_bytes = [f"frame-{index}".encode() for index in range(5)]
    steady = iter(frame_bytes[2:])

    class ScriptedOcr:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def recognise(self, frame: bytes, *, content_type: str) -> SimpleNamespace:
            assert content_type == "image/png"
            index = frame_bytes.index(frame)
            return SimpleNamespace(
                visible_text="SUGAR" if index == 1 else "SODIUM CHLORIDE"
            )

    monkeypatch.setattr(simulation, "MANIFEST", manifest)
    monkeypatch.setattr(simulation, "OUT", tmp_path / "results")
    monkeypatch.setattr(
        simulation, "RULE", ScanRule(required_consecutive=3, interval_ms=400, timeout_ms=2000)
    )
    monkeypatch.setattr(simulation, "EasyOcrLabelRecognition", ScriptedOcr)
    monkeypatch.setattr(
        simulation, "blurred", lambda _image, radius: frame_bytes[0 if radius == 6 else 1]
    )
    monkeypatch.setattr(simulation, "perturb", lambda _image, _rng: next(steady))
    monkeypatch.setattr(sys, "argv", ["live_scan_simulation"])

    assert simulation.main() == 0
    result = json.loads((tmp_path / "results" / "live-scan-simulation-v3.json").read_text())
    assert result["records"][0]["live_status"] == "stable"
    assert result["records"][0]["live_scan"] == "MATCH"
    assert result["records"][0]["probe_readings"] == [
        ["MATCH", "sodium chloride"],
        ["MISMATCH", "sugar"],
        ["MATCH", "sodium chloride"],
        ["MATCH", "sodium chloride"],
    ]
