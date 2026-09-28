"""Failed model calls leave bounded technical provenance and no domain mutation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from labjarvis.api.app import create_app
from labjarvis.infrastructure.documents import DOCX_MIME
from labjarvis.infrastructure.model_errors import ModelUnavailableError
from labjarvis.settings import Settings

SECRET = "PRIVATE-PROTOCOL-CONTENT-DO-NOT-STORE"


def sample_protocol() -> bytes:
    return (
        Path(__file__).parents[4]
        / "sample_data"
        / "protocols"
        / "development-only-ethanol-protocol.docx"
    ).read_bytes()


def start(client: TestClient) -> tuple[str, int]:
    imported = client.post(
        "/api/v1/protocols/upload",
        files={"file": ("development-only.docx", sample_protocol(), DOCX_MIME)},
    ).json()
    started = client.post(
        f"/api/v1/protocol-versions/{imported['version']['id']}/review-approve-and-start",
        headers={"If-Match": '"0"', "Idempotency-Key": "failed-inference-approve"},
        json={"steps": imported["version"]["steps"]},
    ).json()["session"]
    active = client.post(
        f"/api/v1/sessions/{started['id']}/start",
        headers={"If-Match": f'"{started["version"]}"'},
    ).json()
    return active["id"], int(active["version"])


class FailingSpeech:
    adapter_name = "failing-speech"
    model_name = "speech-fixture-v1"

    def transcribe(self, audio: bytes, *, content_type: str, vocabulary: tuple[str, ...] = ()):
        del audio, content_type
        raise ModelUnavailableError(f"speech unavailable {SECRET}")


class FailingText:
    adapter_name = "failing-text"
    model_name = "text-fixture-v1"

    def interpret(self, transcript: str, context):
        del transcript, context
        raise ModelUnavailableError(f"text unavailable {SECRET}")


class FailingVision:
    adapter_name = "failing-vision"
    model_name = "vision-fixture-v1"

    def recognise(self, image: bytes, *, content_type: str):
        del image, content_type
        raise ModelUnavailableError(f"vision unavailable {SECRET}")


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        app_env="test",
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'failed-inference.db').as_posix()}",
    )
    with TestClient(create_app(settings, create_schema=True)) as test_client:
        yield test_client


def assert_one_safe_failure(client: TestClient, session_id: str, modality: str) -> None:
    review = client.get(f"/api/v1/sessions/{session_id}/notebook").json()
    failures = [
        item
        for item in review["inferences"]
        if item["status"] == "failed" and item["modality"] == modality
    ]
    assert len(failures) == 1
    failure = failures[0]
    assert failure["input_sha256"]
    assert failure["adapter"]
    assert failure["model_name"]
    assert failure["output"]["failure_code"] == "MODEL_UNAVAILABLE"
    assert SECRET not in json.dumps(failure)
    assert review["entries"] == []


def test_text_failure_is_recorded_after_command_transaction_rolls_back(client) -> None:
    session_id, version = start(client)
    client.app.state.service._text = FailingText()

    response = client.post(
        f"/api/v1/sessions/{session_id}/commands/text",
        headers={"If-Match": f'"{version}"', "Idempotency-Key": "failed-text"},
        json={"transcript": f"Record observation {SECRET}"},
    )

    assert response.status_code == 503
    assert_one_safe_failure(client, session_id, "text")


def test_speech_failure_is_recorded_after_command_transaction_rolls_back(client) -> None:
    session_id, version = start(client)
    client.app.state.service._speech = FailingSpeech()

    response = client.post(
        f"/api/v1/sessions/{session_id}/commands/audio",
        headers={"If-Match": f'"{version}"', "Idempotency-Key": "failed-speech"},
        files={"file": ("command.webm", SECRET.encode(), "audio/webm")},
    )

    assert response.status_code == 503
    assert_one_safe_failure(client, session_id, "speech")


def test_high_integrity_transcription_failure_is_recorded_without_primary_success(
    client,
) -> None:
    session_id, version = start(client)
    client.app.state.service._verification_speech = FailingSpeech()

    response = client.post(
        f"/api/v1/sessions/{session_id}/commands/audio",
        headers={"If-Match": f'"{version}"', "Idempotency-Key": "failed-verifier"},
        files={
            "file": (
                "command.webm",
                b"FAKE:record the volume as 10 mL",
                "audio/webm",
            )
        },
    )

    assert response.status_code == 503
    assert_one_safe_failure(client, session_id, "speech_verification")


def test_vision_failure_is_recorded_without_verification_or_notebook_rows(client) -> None:
    session_id, version = start(client)
    client.app.state.service._labels = FailingVision()

    response = client.post(
        f"/api/v1/sessions/{session_id}/reagent-verifications",
        headers={"If-Match": f'"{version}"', "Idempotency-Key": "failed-vision"},
        files={"file": ("label.jpg", SECRET.encode(), "image/jpeg")},
    )

    assert response.status_code == 503
    assert_one_safe_failure(client, session_id, "vision")
    review = client.get(f"/api/v1/sessions/{session_id}/notebook").json()
    assert review["verifications"] == []


def test_spoken_confirmation_failure_is_recorded_after_decision_transaction_rolls_back(
    client,
) -> None:
    session_id, version = start(client)
    proposal = client.post(
        f"/api/v1/sessions/{session_id}/tools/record_observation",
        headers={"If-Match": f'"{version}"', "Idempotency-Key": "spoken-proposal"},
        json={"parameters": {"observation": "clear solution"}},
    ).json()["pending_action"]
    client.app.state.service._speech = FailingSpeech()

    response = client.post(
        f"/api/v1/pending-actions/{proposal['id']}/spoken-confirmation",
        headers={"Idempotency-Key": "failed-spoken-confirmation"},
        files={"file": ("confirmation.webm", b"FAKE:confirm", "audio/webm")},
    )

    assert response.status_code == 503
    assert_one_safe_failure(client, session_id, "speech_confirmation")
    restored = client.get(f"/api/v1/sessions/{session_id}/snapshot").json()
    assert restored["pending_action"]["id"] == proposal["id"]
