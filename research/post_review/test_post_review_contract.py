import hashlib
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_frozen_september_config_preserves_original_results_and_hashes_inputs() -> None:
    config_path = ROOT / "research" / "post_review" / "configs" / "camera-ready.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert config["result_root"] == "research/post_review/results"
    assert config["original_results_commit"] == "d5b7f04837c647a875ab34bc743d8b5dda38a79a"
    manifest = ROOT / config["datasets"]["text"]
    expected_hash = hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert config["dataset_sha256"]["text"] == expected_hash
    assert config["hosted"]["model_name"] == "gpt-5.6-terra"
    assert config["hosted"]["credential_env"] == "LABJARVIS_HOSTED_API_KEY"


def test_original_raw_results_remain_byte_identical_to_recorded_hashes() -> None:
    hashes_path = ROOT / "research" / "post_review" / "original-result-hashes.json"
    recorded = json.loads(hashes_path.read_text(encoding="utf-8"))

    assert set(recorded) == {
        "e1-speech-results.json",
        "e2-text-results.json",
        "e3-vision-results.json",
        "e4-scenario-results.json",
    }
    for name, expected_hash in recorded.items():
        path = ROOT / "research" / "results" / "raw" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_hash
