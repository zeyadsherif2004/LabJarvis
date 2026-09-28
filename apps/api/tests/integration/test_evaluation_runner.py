import json
from pathlib import Path

from labjarvis.evaluation.run import run_evaluation


def test_development_evaluation_writes_honestly_labelled_results(tmp_path: Path) -> None:
    root = Path(__file__).parents[4]
    result_path = run_evaluation(
        root / "evaluation" / "configs" / "development.yaml",
        output_dir=tmp_path,
    )

    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["evidence_class"] == "synthetic_development_only"
    assert result["eligible_for_final_claims"] is False
    assert {candidate["modality"] for candidate in result["candidates"]} == {
        "speech",
        "text",
        "vision",
    }
    assert all(candidate["sample_count"] > 0 for candidate in result["candidates"])
    candidates = {candidate["modality"]: candidate for candidate in result["candidates"]}
    assert candidates["speech"]["metrics"]["process_rss_mb"] > 0
    assert candidates["text"]["metrics"] | {
        "slot_precision": 1.0,
        "slot_recall": 1.0,
        "slot_f1": 1.0,
        "unsupported_rejected": 1.0,
        "unsafe_action": 0.0,
    } == candidates["text"]["metrics"]
    assert candidates["vision"]["metrics"] | {
        "match_precision": 1.0,
        "match_recall": 1.0,
        "match_f1": 1.0,
    } == candidates["vision"]["metrics"]
    assert (tmp_path / "development-summary.csv").is_file()
