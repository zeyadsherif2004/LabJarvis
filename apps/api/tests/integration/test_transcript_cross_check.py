"""A disagreement between two readings of one utterance never changes a record.

The headline case is 5 mL heard as 50 mL. When the low-latency path and the
high-accuracy path disagree about a number, a unit, or a negation, LabJarvis
must stop and ask rather than choose - and it must leave no proposal behind for
someone to confirm later without realising there was a conflict.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from labjarvis.api.app import create_app
from labjarvis.infrastructure.documents import DOCX_MIME
from labjarvis.settings import Settings


def sample_protocol() -> bytes:
    path = (
        Path(__file__).parents[4]
        / "sample_data"
        / "protocols"
        / "development-only-ethanol-protocol.docx"
    )
    return path.read_bytes()


@pytest.fixture
def cross_checking_settings(tmp_path) -> Settings:
    return Settings(
        app_env="test",
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'cross-check.db').as_posix()}",
        high_integrity_transcription_enabled=True,
        high_integrity_transcription_model_name="fake-speech-verification-v1",
    )


def _start(client: TestClient) -> tuple[str, int]:
    imported = client.post(
        "/api/v1/protocols/upload",
        files={"file": ("development-only.docx", sample_protocol(), DOCX_MIME)},
    ).json()
    version_id = imported["version"]["id"]
    reviewed = client.put(
        f"/api/v1/protocol-versions/{version_id}",
        headers={"If-Match": '"0"'},
        json={"steps": imported["version"]["steps"]},
    ).json()
    body = client.post(
        f"/api/v1/protocol-versions/{version_id}/approve-and-start",
        headers={"If-Match": f'"{reviewed["revision"]}"'},
    ).json()
    session_id = body["session"]["id"]
    started = client.post(
        f"/api/v1/sessions/{session_id}/start",
        headers={"If-Match": f'"{body["session"]["version"]}"'},
    ).json()
    return session_id, int(started["version"])


def _speak(client: TestClient, session_id: str, version: int, fixture: str, key: str):
    return client.post(
        f"/api/v1/sessions/{session_id}/commands/audio",
        headers={"If-Match": f'"{version}"', "Idempotency-Key": key},
        files={"file": ("command.webm", f"FAKE:{fixture}".encode(), "audio/webm")},
    )


def test_a_five_versus_fifty_millilitre_disagreement_records_nothing(
    cross_checking_settings,
) -> None:
    with TestClient(create_app(cross_checking_settings, create_schema=True)) as client:
        session_id, version = _start(client)

        response = _speak(
            client,
            session_id,
            version,
            "record the volume as 5 mL||record the volume as 50 mL",
            "conflict-1",
        )

        assert response.status_code == 202, response.text
        body = response.json()
        assert body["intent"] == "TRANSCRIPT_CONFLICT"
        # No proposal exists, so nobody can confirm the wrong value later.
        assert body["pending_action"] is None
        assert "5" in body["response_text"] or "two different ways" in body["response_text"]

        notebook = client.get(f"/api/v1/sessions/{session_id}/notebook").json()
        measurements = [
            entry for entry in notebook["entries"] if entry["entry_type"] == "measurement"
        ]
        assert measurements == []
        conflicts = [
            entry for entry in notebook["entries"] if entry["entry_type"] == "transcript_conflict"
        ]
        assert len(conflicts) == 1


def test_a_unit_disagreement_is_treated_the_same_as_a_value_disagreement(
    cross_checking_settings,
) -> None:
    with TestClient(create_app(cross_checking_settings, create_schema=True)) as client:
        session_id, version = _start(client)

        body = _speak(
            client,
            session_id,
            version,
            "record the volume as 10 mL||record the volume as 10 uL",
            "conflict-unit",
        ).json()

        assert body["intent"] == "TRANSCRIPT_CONFLICT"
        assert body["pending_action"] is None


def test_agreeing_readings_proceed_to_a_normal_confirmable_proposal(
    cross_checking_settings,
) -> None:
    with TestClient(create_app(cross_checking_settings, create_schema=True)) as client:
        session_id, version = _start(client)

        body = _speak(
            client,
            session_id,
            version,
            "record the volume as 10 mL||record the volume as 10 millilitres",
            "agree-1",
        ).json()

        # Wording differs; meaning does not. Asking the operator to repeat this
        # would make hands-free operation unusable for no safety gain.
        assert body["intent"] == "RECORD_MEASUREMENT"
        assert body["pending_action"] is not None
        assert body["pending_action"]["payload"]["value"] == "10"
        assert body["pending_action"]["payload"]["unit"] == "mL"


def test_both_readings_are_stored_with_their_models_for_later_review(
    cross_checking_settings,
) -> None:
    with TestClient(create_app(cross_checking_settings, create_schema=True)) as client:
        session_id, version = _start(client)
        _speak(
            client,
            session_id,
            version,
            "record the volume as 5 mL||record the volume as 50 mL",
            "provenance-1",
        )

        notebook = client.get(f"/api/v1/sessions/{session_id}/notebook").json()
        modalities = {item["modality"] for item in notebook["inferences"]}

        # Both the primary and the verification reading are recorded, each
        # attributed to the model that produced it.
        assert "speech" in modalities
        assert "speech_verification" in modalities
        events = {item["event_type"] for item in notebook["audit_events"]}
        assert "transcript_cross_checked" in events


def test_a_harmless_question_is_not_transcribed_twice(cross_checking_settings) -> None:
    """Escalation is selective: re-reading this would cost latency and protect nothing."""

    with TestClient(create_app(cross_checking_settings, create_schema=True)) as client:
        session_id, version = _start(client)

        _speak(client, session_id, version, "what is the next step", "cheap-1")

        notebook = client.get(f"/api/v1/sessions/{session_id}/notebook").json()
        verifications = [
            item for item in notebook["inferences"] if item["modality"] == "speech_verification"
        ]
        assert verifications == []


def test_cross_checking_is_off_unless_it_is_configured(tmp_path) -> None:
    settings = Settings(
        app_env="test",
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'single.db').as_posix()}",
    )
    with TestClient(create_app(settings, create_schema=True)) as client:
        session_id, version = _start(client)

        body = _speak(
            client,
            session_id,
            version,
            "record the volume as 5 mL||record the volume as 50 mL",
            "single-path",
        ).json()

        # With one reader there is nothing to compare, so the primary reading is
        # proposed as normal and the operator confirmation remains the safeguard.
        assert body["intent"] == "RECORD_MEASUREMENT"
        assert body["pending_action"]["payload"]["value"] == "5"


def test_enabling_cross_checking_without_a_model_identifier_is_refused() -> None:
    with pytest.raises(ValueError, match="HIGH_INTEGRITY_TRANSCRIPTION_MODEL_NAME"):
        Settings(
            app_env="test",
            high_integrity_transcription_enabled=True,
            high_integrity_transcription_model_name="",
        )
