from hashlib import sha256
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from labjarvis.api.app import create_app
from labjarvis.infrastructure.database import (
    audit_events_table,
    idempotency_records_table,
    model_inferences_table,
    notebook_entries_table,
    pending_actions_table,
    reagent_verifications_table,
    session_timers_table,
    sessions_table,
    utterances_table,
)
from labjarvis.infrastructure.documents import DOCX_MIME
from labjarvis.settings import Settings


def _sample_protocol() -> bytes:
    return (
        Path(__file__).parents[4]
        / "sample_data"
        / "protocols"
        / "development-only-ethanol-protocol.docx"
    ).read_bytes()


def _create_approved_session(client: TestClient) -> tuple[str, str]:
    imported = client.post(
        "/api/v1/protocols/upload",
        files={"file": ("protocol.docx", _sample_protocol(), DOCX_MIME)},
    ).json()
    version_id = imported["version"]["id"]
    reviewed = client.put(
        f"/api/v1/protocol-versions/{version_id}",
        headers={"If-Match": '"0"'},
        json={"steps": imported["version"]["steps"]},
    )
    assert reviewed.status_code == 200
    approved = client.post(
        f"/api/v1/protocol-versions/{version_id}/approve",
        headers={"If-Match": '"1"'},
    )
    assert approved.status_code == 200
    created = client.post(
        "/api/v1/sessions",
        json={"protocol_version_id": version_id, "label": "Deletion fixture"},
    )
    assert created.status_code == 201
    return imported["protocol_id"], created.json()["id"]


def _session_counts(client: TestClient, session_id: str) -> dict[str, int]:
    session_factory = client.app.state.session_factory
    tables = (
        sessions_table,
        pending_actions_table,
        notebook_entries_table,
        model_inferences_table,
        reagent_verifications_table,
        session_timers_table,
        utterances_table,
        audit_events_table,
    )
    with session_factory() as database:
        counts = {
            table.name: int(
                database.execute(
                    select(func.count()).select_from(table).where(table.c.session_id == session_id)
                    if table is not sessions_table
                    else select(func.count())
                    .select_from(table)
                    .where(table.c.id == session_id)
                ).scalar_one()
            )
            for table in tables
        }
        counts["idempotency_records"] = int(
            database.execute(
                select(func.count())
                .select_from(idempotency_records_table)
                .where(idempotency_records_table.c.scope.contains(session_id))
            ).scalar_one()
        )
    return counts


def test_delete_session_removes_aggregate_media_and_writes_minimal_receipt(tmp_path) -> None:
    settings = Settings(
        app_env="test",
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{tmp_path / 'data-management.db'}",
        retain_raw_media=True,
    )
    with TestClient(create_app(settings, create_schema=True)) as client:
        protocol_id, session_id = _create_approved_session(client)
        started = client.post(
            f"/api/v1/sessions/{session_id}/start",
            headers={"If-Match": '"0"'},
        )
        assert started.status_code == 200
        verification = client.post(
            f"/api/v1/sessions/{session_id}/reagent-verifications",
            headers={"If-Match": '"1"', "Idempotency-Key": "delete-media-fixture"},
            files={"file": ("label.txt", b"FAKE:ethanol", "image/png")},
        )
        assert verification.status_code == 200
        before = _session_counts(client, session_id)
        assert before["sessions"] == 1
        assert before["model_inferences"] >= 1
        assert before["reagent_verifications"] >= 1
        assert before["notebook_entries"] >= 1
        assert before["audit_events"] >= 1
        assert before["idempotency_records"] >= 1
        retained = list((tmp_path / "raw-media").rglob("*.bin"))
        assert retained

        refused = client.request(
            "DELETE",
            f"/api/v1/data/protocols/{protocol_id}",
            json={"confirmation": f"DELETE {protocol_id}"},
        )
        assert refused.status_code == 409
        assert refused.json()["code"] == "PROTOCOL_DELETE_REFERENCED"

        mismatch = client.request(
            "DELETE",
            f"/api/v1/data/sessions/{session_id}",
            json={"confirmation": "DELETE something-else"},
        )
        assert mismatch.status_code == 422
        assert mismatch.json()["code"] == "DELETION_CONFIRMATION_MISMATCH"
        assert _session_counts(client, session_id)["sessions"] == 1

        response = client.request(
            "DELETE",
            f"/api/v1/data/sessions/{session_id}",
            json={"confirmation": f"DELETE {session_id}"},
        )
        assert response.status_code == 200
        assert all(count == 0 for count in _session_counts(client, session_id).values())
        assert not list((tmp_path / "raw-media").rglob("*.bin"))

        receipt = response.json()
        assert receipt["object_type"] == "session"
        assert receipt["object_id_hash"] == sha256(session_id.encode()).hexdigest()
        assert receipt["cleanup_state"] == "complete"
        assert "session_id" not in receipt
        assert session_id not in (tmp_path / "deletion-receipts" / "receipts.jsonl").read_text()


def test_delete_unused_draft_protocol_requires_exact_confirmation(tmp_path) -> None:
    settings = Settings(
        app_env="test",
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{tmp_path / 'draft-delete.db'}",
    )
    with TestClient(create_app(settings, create_schema=True)) as client:
        imported = client.post(
            "/api/v1/protocols/upload",
            files={"file": ("protocol.docx", _sample_protocol(), DOCX_MIME)},
        ).json()
        protocol_id = imported["protocol_id"]

        response = client.request(
            "DELETE",
            f"/api/v1/data/protocols/{protocol_id}",
            json={"confirmation": f"DELETE {protocol_id}"},
        )
        assert response.status_code == 200
        assert response.json()["object_type"] == "protocol"
        assert client.get(f"/api/v1/protocols/{protocol_id}").status_code == 404
