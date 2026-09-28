"""Discovery, resume, durable timers, and the deterministic tool gateway.

These exercise the properties a hands-free operator actually depends on: work
can be found again, an interrupted session comes back exactly as it was, timers
keep running across a restart, and a button press reaches the database without
passing through a language model.
"""

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from labjarvis.api.app import create_app
from labjarvis.infrastructure.database import SqlAlchemyUnitOfWork
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
def settings(tmp_path) -> Settings:
    return Settings(
        app_env="test",
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'resume.db').as_posix()}",
    )


def _import_and_start(client: TestClient) -> tuple[str, str, dict]:
    """Import, review, approve-and-start, then activate. Returns ids and the session."""

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
    response = client.post(
        f"/api/v1/protocol-versions/{version_id}/approve-and-start",
        headers={"If-Match": f'"{reviewed["revision"]}"'},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    session = body["session"]
    started = client.post(
        f"/api/v1/sessions/{session['id']}/start",
        headers={"If-Match": f'"{session["version"]}"'},
    )
    assert started.status_code == 200, started.text
    return version_id, session["id"], started.json()


def _invoke(
    client: TestClient,
    session_id: str,
    tool: str,
    version: int,
    parameters: dict | None = None,
    *,
    key: str | None = None,
) -> dict:
    response = client.post(
        f"/api/v1/sessions/{session_id}/tools/{tool}",
        headers={
            "If-Match": f'"{version}"',
            "Idempotency-Key": key or f"{tool}-{version}",
        },
        json={"parameters": parameters or {}},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _advance_session_version_out_of_band(client: TestClient, session_id: str) -> None:
    """Simulate a historical/restarted writer that made an old proposal stale."""

    with SqlAlchemyUnitOfWork(client.app.state.session_factory) as uow:
        session = uow.sessions.get(session_id)
        assert session is not None
        uow.sessions.save(
            replace(session, version=session.version + 1),
            expected_version=session.version,
        )


# --------------------------------------------------------------------- discovery


def test_protocols_and_sessions_can_be_discovered_without_a_bookmark(settings) -> None:
    with TestClient(create_app(settings, create_schema=True)) as client:
        _, session_id, _ = _import_and_start(client)

        protocols = client.get("/api/v1/protocols").json()["protocols"]
        assert len(protocols) == 1
        assert protocols[0]["latest_version_status"] == "APPROVED"
        assert protocols[0]["step_count"] >= 1
        assert protocols[0]["session_count"] == 1

        sessions = client.get("/api/v1/sessions?resumable_only=true").json()["sessions"]
        assert [item["session_id"] for item in sessions] == [session_id]
        assert sessions[0]["resumable"] is True
        assert sessions[0]["protocol_title"]
        assert sessions[0]["active_step_number"] == 1


def test_protocol_search_narrows_by_title(settings) -> None:
    with TestClient(create_app(settings, create_schema=True)) as client:
        _import_and_start(client)

        assert client.get("/api/v1/protocols?q=development").json()["protocols"]
        assert client.get("/api/v1/protocols?q=zzz-no-match").json()["protocols"] == []


# ------------------------------------------------------------------------ resume


def test_a_snapshot_restores_the_whole_session_view_from_authority(settings) -> None:
    with TestClient(create_app(settings, create_schema=True)) as client:
        version_id, session_id, started = _import_and_start(client)
        _invoke(client, session_id, "set_timer", started["version"], {"duration_seconds": "300"})

        snapshot = client.get(f"/api/v1/sessions/{session_id}/snapshot")

        assert snapshot.status_code == 200, snapshot.text
        body = snapshot.json()
        assert body["session"]["id"] == session_id
        assert body["version"]["id"] == version_id
        assert body["version"]["status"] == "APPROVED"
        assert body["protocol_title"]
        assert len(body["timers"]) == 1
        assert body["timers"][0]["state"] == "RUNNING"
        # The snapshot alone is enough to render; no conversation history needed.
        assert body["session"]["active_step_id"] is not None


def test_a_session_survives_a_backend_restart_with_its_timer_intact(settings) -> None:
    """The second TestClient is a fresh application against the same database."""

    with TestClient(create_app(settings, create_schema=True)) as client:
        _, session_id, started = _import_and_start(client)
        _invoke(client, session_id, "set_timer", started["version"], {"duration_seconds": "600"})

    with TestClient(create_app(settings, create_schema=True)) as restarted:
        body = restarted.get(f"/api/v1/sessions/{session_id}/snapshot").json()

        assert body["session"]["state"] == "ACTIVE"
        timer = body["timers"][0]
        assert timer["state"] == "RUNNING"
        assert 0 < timer["remaining_seconds"] <= 600
        assert timer["deadline_at"] is not None


def test_an_unconfirmed_proposal_is_visible_after_rehydration(settings) -> None:
    with TestClient(create_app(settings, create_schema=True)) as client:
        _, session_id, started = _import_and_start(client)
        proposed = _invoke(
            client,
            session_id,
            "record_measurement",
            started["version"],
            {"kind": "volume", "value": "10", "unit": "mL"},
        )
        assert proposed["committed"] is False
        action_id = proposed["pending_action"]["id"]

    with TestClient(create_app(settings, create_schema=True)) as restarted:
        body = restarted.get(f"/api/v1/sessions/{session_id}/snapshot").json()

        # A reconnecting client sees the outstanding decision instead of
        # proposing the same measurement a second time.
        assert body["pending_action"]["id"] == action_id
        assert body["pending_action"]["intent"] == "RECORD_MEASUREMENT"


def test_a_second_mutation_is_refused_while_a_confirmation_is_pending(settings) -> None:
    with TestClient(create_app(settings, create_schema=True)) as client:
        _, session_id, started = _import_and_start(client)
        first = _invoke(
            client,
            session_id,
            "record_measurement",
            started["version"],
            {"kind": "volume", "value": "10", "unit": "mL"},
            key="first-proposal",
        )

        second = client.post(
            f"/api/v1/sessions/{session_id}/tools/set_timer",
            headers={
                "If-Match": f'"{started["version"]}"',
                "Idempotency-Key": "unrelated-mutation",
            },
            json={"parameters": {"duration_seconds": "120"}},
        )

        assert second.status_code == 409
        assert second.json()["code"] == "PENDING_ACTION_EXISTS"
        restored = client.get(f"/api/v1/sessions/{session_id}/snapshot").json()
        assert restored["pending_action"]["id"] == first["pending_action"]["id"]
        assert restored["timers"] == []


def test_read_only_tool_remains_available_while_confirmation_is_pending(settings) -> None:
    with TestClient(create_app(settings, create_schema=True)) as client:
        _, session_id, started = _import_and_start(client)
        _invoke(
            client,
            session_id,
            "record_measurement",
            started["version"],
            {"kind": "volume", "value": "10", "unit": "mL"},
        )

        current = client.post(
            f"/api/v1/sessions/{session_id}/tools/get_current_step",
            headers={
                "If-Match": f'"{started["version"]}"',
                "Idempotency-Key": "read-while-pending",
            },
            json={"parameters": {}},
        )

        assert current.status_code == 200
        assert current.json()["data"]["step_number"] == "1"


def test_typed_mutation_is_refused_before_inference_while_confirmation_is_pending(
    settings,
) -> None:
    with TestClient(create_app(settings, create_schema=True)) as client:
        _, session_id, started = _import_and_start(client)
        _invoke(
            client,
            session_id,
            "record_measurement",
            started["version"],
            {"kind": "volume", "value": "10", "unit": "mL"},
        )

        response = client.post(
            f"/api/v1/sessions/{session_id}/commands/text",
            headers={
                "If-Match": f'"{started["version"]}"',
                "Idempotency-Key": "typed-while-pending",
            },
            json={"transcript": "Record temperature as 37 degrees Celsius"},
        )

        assert response.status_code == 409
        assert response.json()["code"] == "PENDING_ACTION_EXISTS"


def test_stale_pending_action_can_be_rejected_without_applying_its_payload(settings) -> None:
    with TestClient(create_app(settings, create_schema=True)) as client:
        _, session_id, started = _import_and_start(client)
        proposal = _invoke(
            client,
            session_id,
            "record_measurement",
            started["version"],
            {"kind": "volume", "value": "10", "unit": "mL"},
        )["pending_action"]
        _advance_session_version_out_of_band(client, session_id)

        response = client.post(
            f"/api/v1/pending-actions/{proposal['id']}/confirm",
            json={"accepted": False},
        )

        assert response.status_code == 200
        assert response.json()["pending_action"]["status"] == "REJECTED"
        entries = client.get(f"/api/v1/sessions/{session_id}/notebook").json()["entries"]
        assert [entry for entry in entries if entry["entry_type"] == "measurement"] == []


def test_repeating_the_same_pending_decision_is_idempotent_but_opposite_conflicts(
    settings,
) -> None:
    with TestClient(create_app(settings, create_schema=True)) as client:
        _, session_id, started = _import_and_start(client)
        proposal = _invoke(
            client,
            session_id,
            "record_measurement",
            started["version"],
            {"kind": "volume", "value": "10", "unit": "mL"},
        )["pending_action"]
        endpoint = f"/api/v1/pending-actions/{proposal['id']}/confirm"

        assert client.post(endpoint, json={"accepted": False}).status_code == 200
        repeated = client.post(endpoint, json={"accepted": False})
        opposite = client.post(endpoint, json={"accepted": True})

        assert repeated.status_code == 200
        assert repeated.json()["pending_action"]["status"] == "REJECTED"
        assert opposite.status_code == 409
        assert opposite.json()["code"] == "PENDING_ACTION_DECISION_CONFLICT"


def test_snapshot_expires_a_stale_pending_action_and_audits_the_reason(settings) -> None:
    with TestClient(create_app(settings, create_schema=True)) as client:
        _, session_id, started = _import_and_start(client)
        _invoke(
            client,
            session_id,
            "record_measurement",
            started["version"],
            {"kind": "volume", "value": "10", "unit": "mL"},
        )
        _advance_session_version_out_of_band(client, session_id)

        restored = client.get(f"/api/v1/sessions/{session_id}/snapshot")

        assert restored.status_code == 200
        assert restored.json()["pending_action"] is None
        audit_events = client.get(f"/api/v1/sessions/{session_id}/notebook").json()[
            "audit_events"
        ]
        expired = [
            event
            for event in audit_events
            if event["event_type"] == "pending_action_expired"
        ]
        assert len(expired) == 1
        assert expired[0]["details"]["reason"] == "session_version_changed"


# ------------------------------------------------------------------ tool gateway


def test_a_direct_control_action_commits_without_a_language_model(settings) -> None:
    with TestClient(create_app(settings, create_schema=True)) as client:
        _, session_id, started = _import_and_start(client)

        result = _invoke(client, session_id, "pause_session", started["version"])

        assert result["committed"] is True
        assert result["pending_action"] is None
        assert result["session"]["state"] == "PAUSED"
        # No text inference was needed to pause.
        inferences = client.get(f"/api/v1/sessions/{session_id}/notebook").json()["inferences"]
        assert [item for item in inferences if item["modality"] == "text"] == []


def test_recording_a_measurement_always_produces_a_proposal_not_a_commit(settings) -> None:
    with TestClient(create_app(settings, create_schema=True)) as client:
        _, session_id, started = _import_and_start(client)

        result = _invoke(
            client,
            session_id,
            "record_measurement",
            started["version"],
            {"kind": "volume", "value": "10", "unit": "mL"},
        )

        assert result["committed"] is False
        assert result["pending_action"]["intent"] == "RECORD_MEASUREMENT"
        assert result["session"]["state"] == "ACTIVE"


def test_a_measurement_is_normalised_before_it_is_ever_proposed(settings) -> None:
    with TestClient(create_app(settings, create_schema=True)) as client:
        _, session_id, started = _import_and_start(client)

        result = _invoke(
            client,
            session_id,
            "record_measurement",
            started["version"],
            {"kind": "Volume", "value": "10.50", "unit": "millilitres"},
        )

        payload = result["pending_action"]["payload"]
        assert payload["unit"] == "mL"
        assert payload["value"] == "10.5"
        assert payload["kind"] == "volume"


def test_an_unallowlisted_tool_name_is_refused(settings) -> None:
    with TestClient(create_app(settings, create_schema=True)) as client:
        _, session_id, started = _import_and_start(client)

        response = client.post(
            f"/api/v1/sessions/{session_id}/tools/delete_everything",
            headers={"If-Match": f'"{started["version"]}"', "Idempotency-Key": "x"},
            json={"parameters": {}},
        )

        assert response.status_code == 422
        assert response.json()["code"] == "TOOL_NOT_ALLOWLISTED"


def test_a_tool_illegal_in_the_current_state_is_refused(settings) -> None:
    with TestClient(create_app(settings, create_schema=True)) as client:
        _, session_id, started = _import_and_start(client)

        response = client.post(
            f"/api/v1/sessions/{session_id}/tools/resume_session",
            headers={"If-Match": f'"{started["version"]}"', "Idempotency-Key": "y"},
            json={"parameters": {}},
        )

        assert response.status_code == 422
        assert response.json()["code"] == "TOOL_NOT_LEGAL_IN_STATE"


def test_unknown_parameters_are_rejected_rather_than_ignored(settings) -> None:
    with TestClient(create_app(settings, create_schema=True)) as client:
        _, session_id, started = _import_and_start(client)

        response = client.post(
            f"/api/v1/sessions/{session_id}/tools/pause_session",
            headers={"If-Match": f'"{started["version"]}"', "Idempotency-Key": "z"},
            json={"parameters": {"force": "true"}},
        )

        assert response.status_code == 422
        assert response.json()["code"] == "TOOL_PARAMETERS_INVALID"


def test_replaying_an_idempotency_key_reports_rather_than_repeats(settings) -> None:
    """This is what makes a realtime reconnect safe to retry."""

    with TestClient(create_app(settings, create_schema=True)) as client:
        _, session_id, started = _import_and_start(client)
        first = _invoke(
            client,
            session_id,
            "set_timer",
            started["version"],
            {"duration_seconds": "120"},
            key="reconnect-1",
        )

        second = _invoke(
            client,
            session_id,
            "set_timer",
            started["version"],
            {"duration_seconds": "120"},
            key="reconnect-1",
        )

        assert first["replayed"] is False
        assert second["replayed"] is True
        # Exactly one timer exists despite two submissions.
        timers = client.get(f"/api/v1/sessions/{session_id}/timers").json()["timers"]
        assert len(timers) == 1


def test_the_same_key_with_different_arguments_is_a_conflict(settings) -> None:
    with TestClient(create_app(settings, create_schema=True)) as client:
        _, session_id, started = _import_and_start(client)
        _invoke(
            client,
            session_id,
            "set_timer",
            started["version"],
            {"duration_seconds": "120"},
            key="shared",
        )

        response = client.post(
            f"/api/v1/sessions/{session_id}/tools/set_timer",
            headers={"If-Match": f'"{started["version"]}"', "Idempotency-Key": "shared"},
            json={"parameters": {"duration_seconds": "999"}},
        )

        assert response.status_code == 409
        assert response.json()["code"] == "IDEMPOTENCY_CONFLICT"


# ---------------------------------------------------------------------- timers


def test_a_timer_can_be_paused_and_resumed_and_keeps_its_remainder(settings) -> None:
    with TestClient(create_app(settings, create_schema=True)) as client:
        _, session_id, started = _import_and_start(client)
        created = _invoke(
            client,
            session_id,
            "set_timer",
            started["version"],
            {"duration_seconds": "300"},
        )
        timer_id = created["timers"][0]["id"]

        paused = _invoke(
            client,
            session_id,
            "pause_timer",
            started["version"],
            {"timer_id": timer_id},
            key="pause-1",
        )
        assert paused["timers"][0]["state"] == "PAUSED"
        assert paused["timers"][0]["remaining_seconds"] > 0
        assert paused["timers"][0]["deadline_at"] is None

        resumed = _invoke(
            client,
            session_id,
            "resume_timer",
            started["version"],
            {"timer_id": timer_id},
            key="resume-1",
        )
        assert resumed["timers"][0]["state"] == "RUNNING"
        assert resumed["timers"][0]["deadline_at"] is not None


def test_a_timer_from_another_session_cannot_be_touched(settings) -> None:
    with TestClient(create_app(settings, create_schema=True)) as client:
        _, first_session, first_started = _import_and_start(client)
        created = _invoke(
            client,
            first_session,
            "set_timer",
            first_started["version"],
            {"duration_seconds": "300"},
        )
        foreign_timer = created["timers"][0]["id"]
        _, second_session, second_started = _import_and_start(client)

        response = client.post(
            f"/api/v1/sessions/{second_session}/tools/cancel_timer",
            headers={
                "If-Match": f'"{second_started["version"]}"',
                "Idempotency-Key": "cross-session",
            },
            json={"parameters": {"timer_id": foreign_timer}},
        )

        assert response.status_code == 404
        assert response.json()["code"] == "TIMER_NOT_FOUND"


# ------------------------------------------------------------------ read-only


def test_read_only_tools_answer_from_structured_state(settings) -> None:
    with TestClient(create_app(settings, create_schema=True)) as client:
        _, session_id, started = _import_and_start(client)

        current = _invoke(client, session_id, "get_current_step", started["version"])

        assert current["committed"] is False
        assert current["pending_action"] is None
        assert current["data"]["step_number"] == "1"
        assert current["data"]["source_page"]
        # Answering the current step must not consult a language model at all.
        inferences = client.get(f"/api/v1/sessions/{session_id}/notebook").json()["inferences"]
        assert inferences == []


def test_the_published_tool_registry_matches_the_backend_allowlist(settings) -> None:
    with TestClient(create_app(settings, create_schema=True)) as client:
        body = client.get("/api/v1/tools").json()

        names = {tool["name"] for tool in body["tools"]}
        assert "get_current_step" in names
        assert "record_measurement" in names
        assert body["contract_version"] == "tools-v1"
        measurement = next(
            tool for tool in body["tools"] if tool["name"] == "record_measurement"
        )
        assert measurement["confirmation"] == "always"
        assert measurement["kind"] == "mutating"
