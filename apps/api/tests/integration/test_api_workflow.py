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


def test_complete_fake_http_workflow(tmp_path) -> None:
    settings = Settings(
        app_env="test",
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{tmp_path / 'api-workflow.db'}",
        retain_transcripts=False,
        application_commit="test-commit",
    )
    with TestClient(create_app(settings, create_schema=True)) as client:
        imported_response = client.post(
            "/api/v1/protocols/upload",
            files={"file": ("development-only.docx", sample_protocol(), DOCX_MIME)},
        )
        assert imported_response.status_code == 201
        imported = imported_response.json()
        version_id = imported["version"]["id"]

        reviewed_response = client.put(
            f"/api/v1/protocol-versions/{version_id}",
            headers={"If-Match": '"0"'},
            json={"steps": imported["version"]["steps"]},
        )
        assert reviewed_response.status_code == 200
        assert reviewed_response.json()["revision"] == 1

        approved_response = client.post(
            f"/api/v1/protocol-versions/{version_id}/approve",
            headers={"If-Match": '"1"'},
        )
        assert approved_response.status_code == 200
        assert approved_response.json()["status"] == "APPROVED"

        copied_response = client.post(
            f"/api/v1/protocol-versions/{version_id}/copy",
            headers={"Idempotency-Key": "copy-approved-version"},
        )
        assert copied_response.status_code == 201
        copied = copied_response.json()
        assert copied["version_number"] == 2
        assert copied["status"] == "DRAFT"
        assert copied["steps"] == approved_response.json()["steps"]
        assert copied["source_sha256"] == approved_response.json()["source_sha256"]
        assert client.get(f"/api/v1/protocol-versions/{version_id}").json()["status"] == "APPROVED"

        archived = client.post(
            f"/api/v1/protocols/{imported['protocol_id']}/archive",
            headers={"Idempotency-Key": "archive-protocol"},
        )
        assert archived.status_code == 200
        assert archived.json()["archived_at"] is not None
        assert client.get("/api/v1/protocols").json()["protocols"] == []
        archived_rows = client.get(
            "/api/v1/protocols",
            params={"archived": True},
        ).json()["protocols"]
        assert [row["protocol_id"] for row in archived_rows] == [imported["protocol_id"]]

        restored = client.post(
            f"/api/v1/protocols/{imported['protocol_id']}/restore",
            headers={"Idempotency-Key": "restore-protocol"},
        )
        assert restored.status_code == 200
        assert restored.json()["archived_at"] is None

        overlong = client.post(
            "/api/v1/sessions",
            json={"protocol_version_id": version_id, "label": "x" * 121},
        )
        assert overlong.status_code == 422
        assert overlong.json()["code"] == "SESSION_LABEL_TOO_LONG"

        created_response = client.post(
            "/api/v1/sessions",
            json={"protocol_version_id": version_id, "label": "  Batch   A  "},
        )
        assert created_response.status_code == 201
        session = created_response.json()
        session_id = session["id"]
        assert session["label"] == "Batch A"

        discovered = client.get(
            "/api/v1/sessions",
            params={
                "label": "batch a",
                "state": "READY",
                "from": "2026-01-01T00:00:00Z",
                "to": "2027-01-01T00:00:00Z",
                "protocol_version_id": version_id,
            },
        )
        assert discovered.status_code == 200
        assert [item["session_id"] for item in discovered.json()["sessions"]] == [session_id]

        invalid_range = client.get(
            "/api/v1/sessions",
            params={
                "from": "2027-01-01T00:00:00Z",
                "to": "2026-01-01T00:00:00Z",
            },
        )
        assert invalid_range.status_code == 422
        assert invalid_range.json()["code"] == "DISCOVERY_DATE_RANGE_INVALID"

        started_response = client.post(
            f"/api/v1/sessions/{session_id}/start",
            headers={"If-Match": '"0"'},
        )
        assert started_response.status_code == 200
        active = started_response.json()
        assert active["state"] == "ACTIVE"

        command_response = client.post(
            f"/api/v1/sessions/{session_id}/commands/text",
            headers={"If-Match": '"1"', "Idempotency-Key": "http-command-1"},
            json={"transcript": "Record the temperature as 37 degrees Celsius"},
        )
        assert command_response.status_code == 202
        proposal = command_response.json()
        assert proposal["pending_action"] is not None
        assert "Review" in proposal["response_text"]

        confirmation_response = client.post(
            f"/api/v1/pending-actions/{proposal['pending_action']['id']}/confirm",
            json={"accepted": True},
        )
        assert confirmation_response.status_code == 200
        assert confirmation_response.json()["entry"]["entry_type"] == "measurement"
        reagent_version = confirmation_response.json()["session"]["version"]

        verification_response = client.post(
            f"/api/v1/sessions/{session_id}/reagent-verifications",
            headers={
                "If-Match": f'"{reagent_version}"',
                "Idempotency-Key": "http-reagent-1",
            },
            files={"file": ("fixture.jpg", b"FAKE:ethanol", "image/jpeg")},
        )
        assert verification_response.status_code == 200
        verification = verification_response.json()
        assert verification["outcome"] == "MATCH"
        assert verification["decision"] == "PENDING"
        assert verification["expected_normalised"] == "ethanol"

        confirmed_verification = client.post(
            f"/api/v1/reagent-verifications/{verification['id']}/confirm",
            headers={
                "If-Match": f'"{reagent_version}"',
                "Idempotency-Key": "http-reagent-confirm-1",
            },
            json={"accepted": True, "corrected_observed_label": None},
        )
        assert confirmed_verification.status_code == 200
        assert confirmed_verification.json()["decision"] == "CONFIRMED"
        duplicate_confirmation = client.post(
            f"/api/v1/reagent-verifications/{verification['id']}/confirm",
            headers={
                "If-Match": f'"{reagent_version}"',
                "Idempotency-Key": "http-reagent-confirm-2",
            },
            json={"accepted": True, "corrected_observed_label": None},
        )
        assert duplicate_confirmation.status_code == 409
        assert duplicate_confirmation.json()["code"] == "REAGENT_VERIFICATION_RESOLVED"

        notebook_response = client.get(f"/api/v1/sessions/{session_id}/notebook")
        assert notebook_response.status_code == 200
        review = notebook_response.json()
        assert review["protocol_version_id"] == version_id
        assert any(entry["entry_type"] == "measurement" for entry in review["entries"])
        proposal_entry = next(
            entry for entry in review["entries"] if entry["entry_type"] == "command_proposal"
        )
        assert "transcript" not in proposal_entry["payload"]
        assert proposal_entry["payload"]["transcript_retained"] == "false"
        assert len(proposal_entry["payload"]["transcript_sha256"]) == 64
        assert {inference["modality"] for inference in review["inferences"]} >= {
            "text",
            "vision",
        }
        assert all(inference["model_name"] for inference in review["inferences"])
        assert review["verifications"][0]["decision"] == "CONFIRMED"
        assert any(event["event_type"] == "command_confirmed" for event in review["audit_events"])

        export_response = client.get(
            f"/api/v1/sessions/{session_id}/notebook/export",
            params={"format": "json"},
        )
        assert export_response.status_code == 200
        assert export_response.headers["content-type"].startswith("application/json")
        assert export_response.headers["content-disposition"].startswith("attachment;")
        exported = export_response.json()
        assert exported["schema_name"] == "labjarvis.session"
        assert exported["schema_version"] == "2.0"
        assert exported["application_commit"] == "test-commit"
        assert exported["session"]["protocol_version_id"] == version_id
        assert exported["protocol"]["version"]["id"] == version_id
        assert len(exported["inferences"]) >= 2
        assert exported["audit_events"]
        assert exported["pending_actions"][0]["status"] == "ACCEPTED"

        diagnostics = client.get("/health/ready").json()
        latency = diagnostics["latency_window"]
        assert latency["max_samples"] == 200
        assert latency["sample_count"] >= 2
        assert latency["modalities"]["text"]["sample_count"] >= 1
        assert latency["modalities"]["vision"]["p95_ms"] >= 0


@pytest.mark.parametrize("changed_field", ["source", "parallel_allowed"])
def test_approval_idempotency_fingerprint_covers_every_approved_field(
    tmp_path,
    changed_field: str,
) -> None:
    settings = Settings(
        app_env="test",
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{tmp_path / f'fingerprint-{changed_field}.db'}",
    )
    with TestClient(create_app(settings, create_schema=True)) as client:
        imported = client.post(
            "/api/v1/protocols/upload",
            files={"file": ("development-only.docx", sample_protocol(), DOCX_MIME)},
        ).json()
        version_id = imported["version"]["id"]
        original = imported["version"]["steps"]
        first = client.post(
            f"/api/v1/protocol-versions/{version_id}/review-approve-and-start",
            headers={"If-Match": '"0"', "Idempotency-Key": "complete-fingerprint"},
            json={"steps": original},
        )
        assert first.status_code == 201

        changed = [dict(step) for step in original]
        if changed_field == "source":
            changed[0] = {
                **changed[0],
                "source": {**changed[0]["source"], "excerpt": "Different approved excerpt."},
            }
        else:
            changed[0] = {**changed[0], "parallel_allowed": not changed[0]["parallel_allowed"]}

        conflict = client.post(
            f"/api/v1/protocol-versions/{version_id}/review-approve-and-start",
            headers={"If-Match": '"0"', "Idempotency-Key": "complete-fingerprint"},
            json={"steps": changed},
        )

        assert conflict.status_code == 409
        assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
