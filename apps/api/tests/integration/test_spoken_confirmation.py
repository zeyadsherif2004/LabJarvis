"""A proposal can be answered out loud, and only by a person.

The gap this closes is specific. Realtime voice could already *propose* a
measurement, but there was no deterministic way to accept it without touching
the screen, so the hands-free claim stopped one step short. The tempting fix -
giving the realtime model a ``confirm`` tool - would have let the model that
made the proposal also approve it, which is exactly the authority the whole
architecture withholds from it.

Every test here asserts database state, not response wording.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from labjarvis.api.app import create_app
from labjarvis.application.tools import realtime_tool_schemas
from labjarvis.infrastructure.database import (
    create_session_factory,
    notebook_entries_table,
    pending_actions_table,
    utterances_table,
)
from labjarvis.infrastructure.documents import DOCX_MIME
from labjarvis.settings import Settings

AUDIO = "audio/webm"


def sample_protocol() -> bytes:
    path = (
        Path(__file__).parents[4]
        / "sample_data"
        / "protocols"
        / "development-only-ethanol-protocol.docx"
    )
    return path.read_bytes()


@pytest.fixture()
def api(tmp_path):
    settings = Settings(
        app_env="test",
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'spoken.db').as_posix()}",
        high_integrity_transcription_enabled=True,
        high_integrity_transcription_model_name="fake-speech-verification-v1",
    )
    with TestClient(create_app(settings, create_schema=True)) as client:
        yield client, settings


def start_session(client: TestClient) -> tuple[str, int]:
    imported = client.post(
        "/api/v1/protocols/upload",
        files={"file": ("development-only.docx", sample_protocol(), DOCX_MIME)},
    ).json()
    version_id = imported["version"]["id"]
    body = client.post(
        f"/api/v1/protocol-versions/{version_id}/review-approve-and-start",
        headers={"If-Match": '"0"', "Idempotency-Key": "approve-spoken"},
        json={"steps": imported["version"]["steps"]},
    ).json()
    session_id = body["session"]["id"]
    started = client.post(
        f"/api/v1/sessions/{session_id}/start",
        headers={"If-Match": f'"{body["session"]["version"]}"'},
    ).json()
    return session_id, int(started["version"])


def propose_measurement(client: TestClient, session_id: str, version: int) -> str:
    """Create a pending measurement the way the realtime layer would."""

    response = client.post(
        f"/api/v1/sessions/{session_id}/tools/record_measurement",
        headers={"If-Match": f'"{version}"', "Idempotency-Key": "realtime-propose-1"},
        json={"parameters": {"kind": "volume", "value": "10", "unit": "mL"}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["committed"] is False
    assert body["pending_action"] is not None
    return str(body["pending_action"]["id"])


def speak(client: TestClient, action_id: str, fixture: str, *, key: str) -> dict:
    return client.post(
        f"/api/v1/pending-actions/{action_id}/spoken-confirmation",
        headers={"Idempotency-Key": key},
        files={"file": ("confirmation.webm", f"FAKE:{fixture}".encode(), AUDIO)},
    )


def measurements(settings: Settings) -> list[dict]:
    factory = create_session_factory(settings.database_url, create_schema=True)
    with factory() as connection:
        rows = connection.execute(
            select(notebook_entries_table).where(
                notebook_entries_table.c.entry_type == "measurement"
            )
        ).mappings().all()
    return [dict(row) for row in rows]


def action_status(settings: Settings, action_id: str) -> str:
    factory = create_session_factory(settings.database_url, create_schema=True)
    with factory() as connection:
        return str(
            connection.execute(
                select(pending_actions_table.c.status).where(
                    pending_actions_table.c.id == action_id
                )
            ).scalar_one()
        )


def confirmation_utterances(settings: Settings) -> list[dict]:
    factory = create_session_factory(settings.database_url, create_schema=True)
    with factory() as connection:
        rows = connection.execute(
            select(utterances_table).where(utterances_table.c.purpose == "confirmation")
        ).mappings().all()
    return [dict(row) for row in rows]


# --------------------------------------------------------------- the model


def test_the_realtime_surface_has_no_confirmation_tool(api) -> None:
    """The model may ask for a confirmation; it must not be able to give one."""

    names = {schema["name"] for schema in realtime_tool_schemas()}
    assert not {
        name for name in names if "confirm" in name or "approve" in name or "accept" in name
    }


# --------------------------------------------------------- the happy paths


def test_a_spoken_readback_commits_the_measurement(api) -> None:
    client, settings = api
    session_id, version = start_session(client)
    action_id = propose_measurement(client, session_id, version)

    response = speak(client, action_id, "confirm 10 mL", key="spoken-1")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decision"] == "confirm"
    assert body["committed"] is True
    assert action_status(settings, action_id) == "ACCEPTED"
    recorded = measurements(settings)
    assert len(recorded) == 1
    assert "10" in recorded[0]["payload_json"]

    # The decision is traceable to a specific recording and a specific proposal.
    utterance = confirmation_utterances(settings)[0]
    assert utterance["pending_action_id"] == action_id
    assert utterance["decision"] == "confirm"
    assert len(utterance["audio_sha256"]) == 64


def test_a_spoken_rejection_records_nothing(api) -> None:
    client, settings = api
    session_id, version = start_session(client)
    action_id = propose_measurement(client, session_id, version)

    body = speak(client, action_id, "no", key="spoken-reject").json()

    assert body["decision"] == "reject"
    assert body["committed"] is False
    assert action_status(settings, action_id) == "REJECTED"
    assert measurements(settings) == []


# ------------------------------------------------------ the refusals that matter


def test_a_bare_yes_does_not_record_a_measurement(api) -> None:
    """Saying yes proves a reply, not that the right number was heard."""

    client, settings = api
    session_id, version = start_session(client)
    action_id = propose_measurement(client, session_id, version)

    body = speak(client, action_id, "yes", key="spoken-bare-yes").json()

    assert body["decision"] == "ambiguous"
    assert body["committed"] is False
    assert action_status(settings, action_id) == "PENDING"
    assert measurements(settings) == []
    assert "confirm 10 mL" in body["response_text"]


def test_a_readback_of_the_wrong_value_is_refused(api) -> None:
    """The 5-versus-50 case, caught at the confirmation rather than the proposal."""

    client, settings = api
    session_id, version = start_session(client)
    action_id = propose_measurement(client, session_id, version)

    body = speak(client, action_id, "confirm 50 mL", key="spoken-wrong-value").json()

    assert body["decision"] == "ambiguous"
    assert action_status(settings, action_id) == "PENDING"
    assert measurements(settings) == []
    assert any("50" in reason for reason in body["reasons"])


def test_a_readback_of_the_wrong_unit_is_refused(api) -> None:
    client, settings = api
    session_id, version = start_session(client)
    action_id = propose_measurement(client, session_id, version)

    body = speak(client, action_id, "confirm 10 microlitres", key="spoken-wrong-unit").json()

    assert body["decision"] == "ambiguous"
    assert action_status(settings, action_id) == "PENDING"
    assert measurements(settings) == []


def test_an_ambiguous_reply_leaves_the_proposal_open(api) -> None:
    client, settings = api
    session_id, version = start_session(client)
    action_id = propose_measurement(client, session_id, version)

    body = speak(client, action_id, "maybe 10 mL", key="spoken-hedge").json()

    assert body["decision"] == "ambiguous"
    assert action_status(settings, action_id) == "PENDING"
    assert measurements(settings) == []


def test_a_negated_affirmation_is_never_read_as_yes(api) -> None:
    client, settings = api
    session_id, version = start_session(client)
    action_id = propose_measurement(client, session_id, version)

    body = speak(client, action_id, "that is not correct 10 mL", key="spoken-negated").json()

    assert body["decision"] != "confirm"
    assert measurements(settings) == []


def test_disagreeing_readings_of_the_reply_record_nothing(api) -> None:
    """Two transcriptions of the confirmation must agree before it counts."""

    client, settings = api
    session_id, version = start_session(client)
    action_id = propose_measurement(client, session_id, version)

    body = speak(
        client,
        action_id,
        "confirm 10 mL||confirm 50 mL",
        key="spoken-conflict",
    ).json()

    assert body["decision"] == "ambiguous"
    assert action_status(settings, action_id) == "PENDING"
    assert measurements(settings) == []
    utterance = confirmation_utterances(settings)[0]
    assert utterance["agreement"] == "conflict"


def test_the_proposing_recording_cannot_confirm_itself(api) -> None:
    """A confirmation has to be a new recording, not the proposal played back."""

    client, settings = api
    session_id, version = start_session(client)

    # An audio command creates the proposal and stores its utterance.
    proposed = client.post(
        f"/api/v1/sessions/{session_id}/commands/audio",
        headers={"If-Match": f'"{version}"', "Idempotency-Key": "audio-propose"},
        files={
            "file": (
                "command.webm",
                b"FAKE:record the volume as 10 mL",
                AUDIO,
            )
        },
    ).json()
    action_id = proposed["pending_action"]["id"]

    replayed = speak(
        client,
        action_id,
        "record the volume as 10 mL",
        key="spoken-replay",
    )

    assert replayed.status_code == 409
    assert replayed.json()["code"] == "CONFIRMATION_AUDIO_REPLAYED"
    assert action_status(settings, action_id) == "PENDING"
    assert measurements(settings) == []


def test_an_already_resolved_proposal_cannot_be_confirmed_again(api) -> None:
    client, settings = api
    session_id, version = start_session(client)
    action_id = propose_measurement(client, session_id, version)
    client.post(
        f"/api/v1/pending-actions/{action_id}/confirm",
        json={"accepted": True},
    )

    late = speak(client, action_id, "confirm 10 mL", key="spoken-late")

    assert late.status_code == 409
    assert late.json()["code"] == "PENDING_ACTION_RESOLVED"
    assert len(measurements(settings)) == 1


def test_a_stale_session_version_is_refused(api) -> None:
    client, settings = api
    session_id, version = start_session(client)
    action_id = propose_measurement(client, session_id, version)

    stale = client.post(
        f"/api/v1/pending-actions/{action_id}/spoken-confirmation",
        headers={"Idempotency-Key": "spoken-stale"},
        params={"expected_version": version + 5},
        files={"file": ("confirmation.webm", b"FAKE:confirm 10 mL", AUDIO)},
    )

    assert stale.status_code == 409
    assert stale.json()["code"] == "SESSION_VERSION_CONFLICT"
    assert measurements(settings) == []


def test_a_redelivered_confirmation_records_once(api) -> None:
    client, settings = api
    session_id, version = start_session(client)
    action_id = propose_measurement(client, session_id, version)

    first = speak(client, action_id, "confirm 10 mL", key="spoken-once")
    second = speak(client, action_id, "confirm 10 mL", key="spoken-once")

    assert first.json()["committed"] is True
    assert second.json()["committed"] is True
    assert second.json()["replayed"] is True
    assert len(measurements(settings)) == 1


def test_measurements_cannot_be_confirmed_by_voice_without_cross_checking(tmp_path) -> None:
    """Without the second reader the integrity control is absent, so voice is refused."""

    settings = Settings(
        app_env="test",
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'no-verify.db').as_posix()}",
        high_integrity_transcription_enabled=False,
    )
    with TestClient(create_app(settings, create_schema=True)) as client:
        session_id, version = start_session(client)
        action_id = propose_measurement(client, session_id, version)

        refused = speak(client, action_id, "confirm 10 mL", key="spoken-nover")

        assert refused.status_code in {409, 503}
        assert refused.json()["code"] == "HIGH_INTEGRITY_TRANSCRIPTION_UNAVAILABLE"
        assert action_status(settings, action_id) == "PENDING"
        assert measurements(settings) == []


def test_a_non_measurement_can_be_confirmed_with_a_plain_yes(api) -> None:
    """Completing a step has no value to mishear, so a clear yes is enough."""

    client, settings = api
    session_id, version = start_session(client)
    proposal = client.post(
        f"/api/v1/sessions/{session_id}/tools/complete_step",
        headers={"If-Match": f'"{version}"', "Idempotency-Key": "realtime-complete"},
        json={"parameters": {}},
    ).json()
    action_id = proposal["pending_action"]["id"]

    body = speak(client, action_id, "yes", key="spoken-complete").json()

    assert body["decision"] == "confirm"
    assert body["committed"] is True
    assert action_status(settings, action_id) == "ACCEPTED"
