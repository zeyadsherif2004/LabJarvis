"""Reagent OCR must not hold authority or duplicate durable review records."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from labjarvis.api.app import create_app
from labjarvis.infrastructure.database import (
    SqlAlchemyUnitOfWork,
    model_inferences_table,
    notebook_entries_table,
    reagent_verifications_table,
)
from labjarvis.infrastructure.documents import DOCX_MIME
from labjarvis.ports.models import LabelResult
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
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'reagent-integrity.db').as_posix()}",
    )


def start(client: TestClient) -> tuple[str, int]:
    imported = client.post(
        "/api/v1/protocols/upload",
        files={"file": ("development-only.docx", sample_protocol(), DOCX_MIME)},
    ).json()
    version_id = imported["version"]["id"]
    started = client.post(
        f"/api/v1/protocol-versions/{version_id}/review-approve-and-start",
        headers={"If-Match": '"0"', "Idempotency-Key": "reagent-approve"},
        json={"steps": imported["version"]["steps"]},
    ).json()["session"]
    active = client.post(
        f"/api/v1/sessions/{started['id']}/start",
        headers={"If-Match": f'"{started["version"]}"'},
    ).json()
    return active["id"], int(active["version"])


def upload(
    client: TestClient,
    session_id: str,
    version: int,
    *,
    key: str,
    image: bytes = b"FAKE:ethanol",
):
    return client.post(
        f"/api/v1/sessions/{session_id}/reagent-verifications",
        headers={"If-Match": f'"{version}"', "Idempotency-Key": key},
        files={"file": ("label.jpg", image, "image/jpeg")},
    )


def table_count(client: TestClient, table) -> int:
    with client.app.state.session_factory() as session:
        return int(session.execute(select(func.count()).select_from(table)).scalar_one())


class VersionAdvancingLabelAdapter:
    """Changes session authority from inside recognition to expose an open UOW."""

    def __init__(self, session_factory, session_id: str) -> None:
        self._session_factory = session_factory
        self._session_id = session_id

    def recognise(self, image: bytes, *, content_type: str) -> LabelResult:
        del image, content_type
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.sessions.get(self._session_id)
            assert session is not None
            uow.sessions.save(
                replace(session, version=session.version + 1),
                expected_version=session.version,
            )
        return LabelResult(
            visible_text="ethanol",
            confidence=1.0,
            adapter="authority-changing-fixture",
            model_name="fixture-v1",
        )


def test_authority_change_during_ocr_prevents_every_durable_reagent_write(settings) -> None:
    with TestClient(create_app(settings, create_schema=True)) as client:
        session_id, version = start(client)
        client.app.state.service._labels = VersionAdvancingLabelAdapter(
            client.app.state.session_factory,
            session_id,
        )

        response = upload(client, session_id, version, key="authority-change")

        assert response.status_code == 409
        assert response.json()["code"] == "SESSION_VERSION_CONFLICT"
        assert table_count(client, reagent_verifications_table) == 0
        assert table_count(client, model_inferences_table) == 0
        assert table_count(client, notebook_entries_table) == 0


def test_lost_response_retry_replays_one_verification(settings) -> None:
    with TestClient(create_app(settings, create_schema=True)) as client:
        session_id, version = start(client)

        first = upload(client, session_id, version, key="lost-response")
        second = upload(client, session_id, version, key="lost-response")

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["id"] == first.json()["id"]
        assert table_count(client, reagent_verifications_table) == 1
        assert table_count(client, model_inferences_table) == 1


def test_reagent_idempotency_key_rejects_different_image(settings) -> None:
    with TestClient(create_app(settings, create_schema=True)) as client:
        session_id, version = start(client)
        assert upload(client, session_id, version, key="same-key").status_code == 200

        conflict = upload(
            client,
            session_id,
            version,
            key="same-key",
            image=b"FAKE:not ethanol",
        )

        assert conflict.status_code == 409
        assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"


def test_different_upload_is_refused_while_requirement_review_is_pending(settings) -> None:
    with TestClient(create_app(settings, create_schema=True)) as client:
        session_id, version = start(client)
        assert upload(client, session_id, version, key="first-upload").status_code == 200

        conflict = upload(client, session_id, version, key="second-upload")

        assert conflict.status_code == 409
        assert conflict.json()["code"] == "REAGENT_VERIFICATION_PENDING"
        assert table_count(client, reagent_verifications_table) == 1


def test_stale_reagent_confirmation_marks_review_stale_without_applying_decision(
    settings,
) -> None:
    with TestClient(create_app(settings, create_schema=True)) as client:
        session_id, version = start(client)
        verification = upload(client, session_id, version, key="stale-upload").json()
        with SqlAlchemyUnitOfWork(client.app.state.session_factory) as uow:
            session = uow.sessions.get(session_id)
            assert session is not None
            uow.sessions.save(
                replace(session, version=session.version + 1),
                expected_version=session.version,
            )

        response = client.post(
            f"/api/v1/reagent-verifications/{verification['id']}/confirm",
            headers={"If-Match": f'"{version}"', "Idempotency-Key": "stale-confirm"},
            json={"accepted": True, "corrected_observed_label": None},
        )

        assert response.status_code == 200
        assert response.json()["decision"] == "STALE"
        review = client.get(f"/api/v1/sessions/{session_id}/notebook").json()
        confirmations = [
            entry
            for entry in review["entries"]
            if entry["entry_type"] == "reagent_verification_confirmation"
        ]
        assert confirmations == []


def test_reagent_confirmation_retry_replays_original_decision(settings) -> None:
    with TestClient(create_app(settings, create_schema=True)) as client:
        session_id, version = start(client)
        verification = upload(client, session_id, version, key="decision-upload").json()
        endpoint = f"/api/v1/reagent-verifications/{verification['id']}/confirm"
        headers = {"If-Match": f'"{version}"', "Idempotency-Key": "decision-retry"}
        body = {"accepted": False, "corrected_observed_label": None}

        first = client.post(endpoint, headers=headers, json=body)
        second = client.post(endpoint, headers=headers, json=body)

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["id"] == first.json()["id"]
        review = client.get(f"/api/v1/sessions/{session_id}/notebook").json()
        confirmations = [
            entry
            for entry in review["entries"]
            if entry["entry_type"] == "reagent_verification_confirmation"
        ]
        assert len(confirmations) == 1
