"""Multi-reagent progress is authoritative and survives client rehydration."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from labjarvis.api.app import create_app
from labjarvis.infrastructure.database import SqlAlchemyUnitOfWork
from labjarvis.infrastructure.documents import DOCX_MIME
from labjarvis.settings import Settings


def sample_protocol() -> bytes:
    return (
        Path(__file__).parents[4]
        / "sample_data"
        / "protocols"
        / "development-only-ethanol-protocol.docx"
    ).read_bytes()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        app_env="test",
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'multi-progress.db').as_posix()}",
    )


def start_multi_reagent_session(client: TestClient) -> tuple[str, int, list[dict]]:
    imported = client.post(
        "/api/v1/protocols/upload",
        files={"file": ("development-only.docx", sample_protocol(), DOCX_MIME)},
    ).json()
    steps = imported["version"]["steps"]
    steps[0]["requirements"].append(
        {
            "requirement_id": f"{steps[0]['id']}-req-buffer",
            "name": "buffer",
            "quantity": "250",
            "unit": "uL",
            "aliases": [],
        }
    )
    version_id = imported["version"]["id"]
    created = client.post(
        f"/api/v1/protocol-versions/{version_id}/review-approve-and-start",
        headers={"If-Match": '"0"', "Idempotency-Key": "multi-approve"},
        json={"steps": steps},
    ).json()["session"]
    active = client.post(
        f"/api/v1/sessions/{created['id']}/start",
        headers={"If-Match": f'"{created["version"]}"'},
    ).json()
    return active["id"], int(active["version"]), steps[0]["requirements"]


def test_snapshot_derives_progress_for_every_active_step_requirement(settings) -> None:
    with TestClient(create_app(settings, create_schema=True)) as client:
        session_id, version, requirements = start_multi_reagent_session(client)

        initial = client.get(f"/api/v1/sessions/{session_id}/snapshot").json()
        assert initial["requirement_progress"] == [
            {
                "requirement_id": requirements[0]["requirement_id"],
                "name": "ethanol",
                "status": "unverified",
                "verification_id": None,
            },
            {
                "requirement_id": requirements[1]["requirement_id"],
                "name": "buffer",
                "status": "unverified",
                "verification_id": None,
            },
        ]

        verification = client.post(
            f"/api/v1/sessions/{session_id}/reagent-verifications",
            params={"requirement_id": requirements[0]["requirement_id"]},
            headers={"If-Match": f'"{version}"', "Idempotency-Key": "multi-upload"},
            files={"file": ("label.jpg", b"FAKE:ethanol", "image/jpeg")},
        ).json()
        pending = client.get(f"/api/v1/sessions/{session_id}/snapshot").json()
        assert [item["status"] for item in pending["requirement_progress"]] == [
            "pending_review",
            "unverified",
        ]

        confirmed = client.post(
            f"/api/v1/reagent-verifications/{verification['id']}/confirm",
            headers={"If-Match": f'"{version}"', "Idempotency-Key": "multi-confirm"},
            json={"accepted": True, "corrected_observed_label": None},
        )
        assert confirmed.status_code == 200
        restored = client.get(f"/api/v1/sessions/{session_id}/snapshot").json()
        assert [item["status"] for item in restored["requirement_progress"]] == [
            "confirmed",
            "unverified",
        ]

        second = client.post(
            f"/api/v1/sessions/{session_id}/reagent-verifications",
            params={"requirement_id": requirements[1]["requirement_id"]},
            headers={"If-Match": f'"{version}"', "Idempotency-Key": "buffer-upload"},
            files={"file": ("label.jpg", b"FAKE:buffer", "image/jpeg")},
        ).json()
        rejected = client.post(
            f"/api/v1/reagent-verifications/{second['id']}/confirm",
            headers={"If-Match": f'"{version}"', "Idempotency-Key": "buffer-reject"},
            json={"accepted": False, "corrected_observed_label": None},
        )
        assert rejected.status_code == 200
        after_rejection = client.get(f"/api/v1/sessions/{session_id}/snapshot").json()
        assert [item["status"] for item in after_rejection["requirement_progress"]] == [
            "confirmed",
            "rejected",
        ]

        stale_candidate = client.post(
            f"/api/v1/sessions/{session_id}/reagent-verifications",
            params={"requirement_id": requirements[1]["requirement_id"]},
            headers={"If-Match": f'"{version}"', "Idempotency-Key": "stale-buffer-upload"},
            files={"file": ("label.jpg", b"FAKE:buffer", "image/jpeg")},
        ).json()
        with SqlAlchemyUnitOfWork(client.app.state.session_factory) as uow:
            session = uow.sessions.get(session_id)
            assert session is not None
            uow.sessions.save(
                replace(session, version=session.version + 1),
                expected_version=session.version,
            )
        stale = client.post(
            f"/api/v1/reagent-verifications/{stale_candidate['id']}/confirm",
            headers={"If-Match": f'"{version}"', "Idempotency-Key": "stale-buffer-confirm"},
            json={"accepted": True, "corrected_observed_label": None},
        )
        assert stale.status_code == 200
        assert stale.json()["decision"] == "STALE"
        after_stale = client.get(f"/api/v1/sessions/{session_id}/snapshot").json()
        assert [item["status"] for item in after_stale["requirement_progress"]] == [
            "confirmed",
            "stale",
        ]


def test_unresolved_reagents_force_warned_completion_and_record_status_map(settings) -> None:
    with TestClient(create_app(settings, create_schema=True)) as client:
        session_id, version, requirements = start_multi_reagent_session(client)

        proposed = client.post(
            f"/api/v1/sessions/{session_id}/tools/complete_step",
            headers={"If-Match": f'"{version}"', "Idempotency-Key": "warn-complete"},
            json={"parameters": {}},
        )

        assert proposed.status_code == 200
        body = proposed.json()
        assert body["committed"] is False
        assert body["pending_action"] is not None
        payload = body["pending_action"]["payload"]
        assert json.loads(payload["requirement_progress"]) == {
            requirements[0]["requirement_id"]: "unverified",
            requirements[1]["requirement_id"]: "unverified",
        }
        assert "label review" in payload["requirement_warning"].lower()
        assert "contents" not in payload["requirement_warning"].lower()

        confirmed = client.post(
            f"/api/v1/pending-actions/{body['pending_action']['id']}/confirm",
            json={"accepted": True},
        )
        assert confirmed.status_code == 200
        completed_session = confirmed.json()["session"]
        assert completed_session["steps"][0]["state"] == "COMPLETED"
        assert completed_session["active_step_id"] != requirements[0]["requirement_id"].split(
            "-req-"
        )[0]
        review = client.get(f"/api/v1/sessions/{session_id}/notebook").json()
        completion = next(
            entry
            for entry in review["entries"]
            if entry["payload"].get("tool") == "complete_step"
        )
        assert completion["payload"]["requirement_progress"] == payload[
            "requirement_progress"
        ]
        completed_event = next(
            event for event in review["audit_events"] if event["event_type"] == "step_completed"
        )
        assert completed_event["details"]["requirement_progress"] == payload[
            "requirement_progress"
        ]
