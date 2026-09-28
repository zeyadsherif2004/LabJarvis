"""Study mode: seeded errors are explicit, pre-declared and provenance-labelled."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from labjarvis.infrastructure.fake_models import FakeLabelRecognition, FakeTextUnderstanding
from labjarvis.infrastructure.model_factory import build_model_adapters
from labjarvis.infrastructure.study_mode import (
    STUDY_ACKNOWLEDGEMENT,
    StudyInjector,
    StudyLabelRecognition,
    StudyScript,
    StudyTextUnderstanding,
)
from labjarvis.ports.models import CommandContext
from labjarvis.settings import Settings

CONTEXT = CommandContext("s", "ACTIVE", "step", "Add 5 mL ethanol.")
SCRIPT = {
    "study_id": "desk-study-1",
    "participant": "P03",
    "condition": "readback",
    "block": 1,
    "injections": [
        {"trial": 2, "kind": "measurement_value", "to": "50", "note": "5 mL -> 50 mL"},
        {"trial": 1, "kind": "reagent_label", "to": "METHANOL"},
    ],
}


def script_file(tmp_path: Path) -> Path:
    path = tmp_path / "script.json"
    path.write_text(json.dumps(SCRIPT), encoding="utf-8")
    return path


def test_study_mode_needs_script_and_exact_acknowledgement(tmp_path: Path) -> None:
    path = script_file(tmp_path)
    with pytest.raises(ValidationError, match="needs both"):
        Settings(data_dir=tmp_path, study_mode_script=path)
    with pytest.raises(ValidationError, match="needs both"):
        Settings(data_dir=tmp_path, study_mode_acknowledgement=STUDY_ACKNOWLEDGEMENT)
    with pytest.raises(ValidationError, match="does not match"):
        Settings(data_dir=tmp_path, study_mode_script=path, study_mode_acknowledgement="yes")


def test_study_mode_is_refused_in_production(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="production"):
        Settings(
            data_dir=tmp_path,
            app_env="production",
            study_mode_script=script_file(tmp_path),
            study_mode_acknowledgement=STUDY_ACKNOWLEDGEMENT,
        )


def test_ordinary_settings_never_enable_study_mode(tmp_path: Path) -> None:
    assert Settings(data_dir=tmp_path).study_mode_enabled is False


def test_only_the_scripted_measurement_trial_is_altered_and_it_is_labelled() -> None:
    injector = StudyInjector(StudyScript.model_validate(SCRIPT))
    text = StudyTextUnderstanding(FakeTextUnderstanding(), injector)

    first = text.interpret("record the volume as 5 mL", CONTEXT)
    ignored = text.interpret("next step", CONTEXT)
    second = text.interpret("record the volume as 5 mL", CONTEXT)

    assert first.command.parameters["value"] == "5"
    assert first.metadata["study_injected"] == "false"
    assert "study_injected" not in ignored.metadata
    assert second.command.parameters["value"] == "50"
    assert second.metadata["study_injected"] == "true"
    assert second.metadata["study_original_value"] == "5"
    assert second.metadata["study_trial"] == "2"
    assert second.metadata["study_participant"] == "P03"


def test_scripted_label_trial_is_replaced_and_labelled() -> None:
    injector = StudyInjector(StudyScript.model_validate(SCRIPT))
    labels = StudyLabelRecognition(FakeLabelRecognition(), injector)

    first = labels.recognise(b"FAKE:ethanol", content_type="image/jpeg")
    second = labels.recognise(b"FAKE:ethanol", content_type="image/jpeg")

    assert first.visible_text == "METHANOL"
    assert first.metadata["study_injected"] == "true"
    assert first.metadata["study_original_text"] == "ethanol"
    assert second.visible_text == "ethanol"
    assert second.metadata["study_injected"] == "false"


def test_factory_wraps_adapters_only_when_study_mode_is_enabled(tmp_path: Path) -> None:
    normal = build_model_adapters(Settings(data_dir=tmp_path))
    study = build_model_adapters(
        Settings(
            data_dir=tmp_path,
            study_mode_script=script_file(tmp_path),
            study_mode_acknowledgement=STUDY_ACKNOWLEDGEMENT,
        )
    )

    assert not isinstance(normal.text, StudyTextUnderstanding)
    assert isinstance(study.text, StudyTextUnderstanding)
    assert isinstance(study.labels, StudyLabelRecognition)


def test_participant_codes_are_pseudonymous() -> None:
    with pytest.raises(ValidationError):
        StudyScript.model_validate({**SCRIPT, "participant": "Jane Doe"})
