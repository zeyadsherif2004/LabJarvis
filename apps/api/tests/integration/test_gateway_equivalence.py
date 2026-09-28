"""The same action proposed four ways is validated the same way.

LabJarvis accepts instructions from a typed box, a push-to-talk recording, a
realtime function call, and a button. The documentation calls this "one
deterministic gateway"; these tests are what makes that claim checkable rather
than aspirational.

The adapters are allowed to differ - a button states its intent unambiguously,
speech does not - but the policies that decide whether something is legal, well
formed, and whether a human has to agree to it must not.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from labjarvis.api.app import create_app
from labjarvis.application.tools import TOOLS, ConfirmationPolicy, ToolKind
from labjarvis.infrastructure.database import (
    create_session_factory,
    notebook_entries_table,
    pending_actions_table,
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
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'equivalence.db').as_posix()}",
    )
    with TestClient(create_app(settings, create_schema=True)) as client:
        yield client, settings


def start(client: TestClient) -> tuple[str, int]:
    imported = client.post(
        "/api/v1/protocols/upload",
        files={"file": ("development-only.docx", sample_protocol(), DOCX_MIME)},
    ).json()
    version_id = imported["version"]["id"]
    body = client.post(
        f"/api/v1/protocol-versions/{version_id}/review-approve-and-start",
        headers={"If-Match": '"0"', "Idempotency-Key": "equivalence-approve"},
        json={"steps": imported["version"]["steps"]},
    ).json()
    session_id = body["session"]["id"]
    started = client.post(
        f"/api/v1/sessions/{session_id}/start",
        headers={"If-Match": f'"{body["session"]["version"]}"'},
    ).json()
    return session_id, int(started["version"])


def pending_rows(settings: Settings) -> list[dict]:
    factory = create_session_factory(settings.database_url, create_schema=True)
    with factory() as connection:
        rows = connection.execute(select(pending_actions_table)).mappings().all()
    return [dict(row) for row in rows]


def measurements(settings: Settings) -> list[dict]:
    factory = create_session_factory(settings.database_url, create_schema=True)
    with factory() as connection:
        rows = (
            connection.execute(
                select(notebook_entries_table).where(
                    notebook_entries_table.c.entry_type == "measurement"
                )
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


# ------------------------------------------------------- confirmation policy


def test_every_record_writing_tool_needs_a_human_whatever_proposed_it() -> None:
    """A registry property, checked directly rather than inferred from one path."""

    for name in (
        "record_measurement",
        "record_observation",
        "complete_step",
        "skip_step_with_reason",
        "end_session",
    ):
        spec = TOOLS[name]
        assert spec.confirmation is ConfirmationPolicy.ALWAYS
        assert spec.requires_confirmation(model_proposed=True)
        assert spec.requires_confirmation(model_proposed=False)


def test_a_reversible_control_commits_from_a_button_but_not_from_a_model() -> None:
    """Documented asymmetry: a click is itself the confirmation, a proposal is not."""

    spec = TOOLS["pause_session"]
    assert spec.confirmation is ConfirmationPolicy.MODEL_PROPOSALS_ONLY
    assert spec.requires_confirmation(model_proposed=True)
    assert not spec.requires_confirmation(model_proposed=False)


def test_the_registry_has_no_mutating_tool_without_a_policy() -> None:
    for spec in TOOLS.values():
        if spec.kind is ToolKind.MUTATING:
            assert spec.confirmation is not ConfirmationPolicy.NONE


# --------------------------------------------------- four paths, one outcome


def test_a_measurement_proposed_four_ways_produces_the_same_proposal(api) -> None:
    client, settings = api
    typed_session, typed_version = start(client)
    audio_session, audio_version = start(client)
    realtime_session, realtime_version = start(client)

    typed = client.post(
        f"/api/v1/sessions/{typed_session}/commands/text",
        headers={"If-Match": f'"{typed_version}"', "Idempotency-Key": "eq-typed"},
        json={"transcript": "Record the volume as 10 mL"},
    ).json()
    audio = client.post(
        f"/api/v1/sessions/{audio_session}/commands/audio",
        headers={"If-Match": f'"{audio_version}"', "Idempotency-Key": "eq-audio"},
        files={"file": ("command.webm", b"FAKE:record the volume as 10 mL", AUDIO)},
    ).json()
    realtime = client.post(
        f"/api/v1/sessions/{realtime_session}/tools/record_measurement",
        headers={
            "If-Match": f'"{realtime_version}"',
            "Idempotency-Key": "eq-realtime",
        },
        json={"parameters": {"kind": "volume", "value": "10", "unit": "mL"}},
    ).json()

    # None of the three committed anything by itself.
    assert typed["pending_action"] is not None
    assert audio["pending_action"] is not None
    assert realtime["committed"] is False
    assert realtime["pending_action"] is not None
    assert measurements(settings) == []

    # And all three produced the same normalised proposal.
    intents = {row["intent"] for row in pending_rows(settings)}
    assert intents == {"RECORD_MEASUREMENT"}
    payloads = [row["payload_json"] for row in pending_rows(settings)]
    assert len(set(payloads)) == 1, payloads


def test_a_direct_ui_action_is_held_to_the_same_rule_for_record_writes(api) -> None:
    """A button cannot record a measurement without confirmation either."""

    client, settings = api
    session_id, version = start(client)

    direct = client.post(
        f"/api/v1/sessions/{session_id}/tools/record_measurement",
        headers={"If-Match": f'"{version}"', "Idempotency-Key": "eq-direct"},
        json={"parameters": {"kind": "volume", "value": "10", "unit": "mL"}},
    ).json()

    assert direct["committed"] is False
    assert direct["pending_action"] is not None
    assert measurements(settings) == []


def test_a_reversible_control_still_commits_directly(api) -> None:
    client, _ = api
    session_id, version = start(client)

    paused = client.post(
        f"/api/v1/sessions/{session_id}/tools/pause_session",
        headers={"If-Match": f'"{version}"', "Idempotency-Key": "eq-pause"},
        json={"parameters": {}},
    ).json()

    assert paused["committed"] is True
    assert paused["pending_action"] is None


# ----------------------------------------------- one validation, every path


def test_an_illegal_state_is_refused_on_every_path(api) -> None:
    """Completing a step is not legal while the session is paused."""

    client, settings = api
    session_id, version = start(client)
    paused = client.post(
        f"/api/v1/sessions/{session_id}/tools/pause_session",
        headers={"If-Match": f'"{version}"', "Idempotency-Key": "eq-pause-2"},
        json={"parameters": {}},
    ).json()
    paused_version = int(paused["session"]["version"])

    gateway = client.post(
        f"/api/v1/sessions/{session_id}/tools/complete_step",
        headers={"If-Match": f'"{paused_version}"', "Idempotency-Key": "eq-complete"},
        json={"parameters": {}},
    )
    typed = client.post(
        f"/api/v1/sessions/{session_id}/commands/text",
        headers={"If-Match": f'"{paused_version}"', "Idempotency-Key": "eq-typed-complete"},
        json={"transcript": "complete the step"},
    )

    assert gateway.status_code == 422
    assert gateway.json()["code"] == "TOOL_NOT_LEGAL_IN_STATE"
    # The typed path answers rather than erroring, but it must refuse just as
    # firmly and must not leave a proposal behind for someone to confirm later.
    assert typed.status_code == 202
    assert typed.json()["pending_action"] is None
    body = typed.json()
    assert body["intent"] == "COMPLETE_STEP"
    assert "not available while the session is paused" in body["response_text"]
    assert pending_rows(settings) == []


def test_an_unparsable_measurement_is_refused_on_every_path(api) -> None:
    client, settings = api
    session_id, version = start(client)

    gateway = client.post(
        f"/api/v1/sessions/{session_id}/tools/record_measurement",
        headers={"If-Match": f'"{version}"', "Idempotency-Key": "eq-bad-gateway"},
        json={"parameters": {"kind": "volume", "value": "not a number", "unit": "mL"}},
    )
    typed = client.post(
        f"/api/v1/sessions/{session_id}/commands/text",
        headers={"If-Match": f'"{version}"', "Idempotency-Key": "eq-bad-typed"},
        json={"transcript": "Record the volume as banana mL"},
    )

    assert gateway.status_code == 422
    assert typed.json()["pending_action"] is None
    assert pending_rows(settings) == []


def test_an_unknown_tool_name_is_refused_before_anything_happens(api) -> None:
    client, settings = api
    session_id, version = start(client)

    response = client.post(
        f"/api/v1/sessions/{session_id}/tools/delete_everything",
        headers={"If-Match": f'"{version}"', "Idempotency-Key": "eq-unknown"},
        json={"parameters": {}},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "TOOL_NOT_ALLOWLISTED"
    assert pending_rows(settings) == []


def test_a_stale_version_is_refused_on_every_path(api) -> None:
    client, settings = api
    session_id, version = start(client)

    gateway = client.post(
        f"/api/v1/sessions/{session_id}/tools/record_measurement",
        headers={"If-Match": f'"{version + 9}"', "Idempotency-Key": "eq-stale-gateway"},
        json={"parameters": {"kind": "volume", "value": "10", "unit": "mL"}},
    )
    typed = client.post(
        f"/api/v1/sessions/{session_id}/commands/text",
        headers={"If-Match": f'"{version + 9}"', "Idempotency-Key": "eq-stale-typed"},
        json={"transcript": "Record the volume as 10 mL"},
    )

    assert gateway.status_code == 409
    assert typed.status_code == 409
    assert pending_rows(settings) == []


def test_every_path_confirms_through_the_same_endpoint(api) -> None:
    """Confirmation is one code path, whatever created the proposal."""

    client, settings = api
    session_id, version = start(client)
    typed = client.post(
        f"/api/v1/sessions/{session_id}/commands/text",
        headers={"If-Match": f'"{version}"', "Idempotency-Key": "eq-confirm-typed"},
        json={"transcript": "Record the volume as 10 mL"},
    ).json()

    confirmed = client.post(
        f"/api/v1/pending-actions/{typed['pending_action']['id']}/confirm",
        json={"accepted": True},
    )

    assert confirmed.status_code == 200
    recorded = measurements(settings)
    assert len(recorded) == 1
    assert "10" in recorded[0]["payload_json"]
