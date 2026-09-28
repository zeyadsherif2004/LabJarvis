"""Review, approval, and session creation must commit together or not at all.

The browser used to review in one request and approve in another. When the
second request failed, the reviewed revision was already in the database while
the browser still held the previous revision number, so every retry failed the
``If-Match`` check and the operator could not tell what had been persisted.

These tests assert the database state rather than any response text: exactly one
approved version and exactly one session, whatever the client did.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from labjarvis.api.app import create_app
from labjarvis.infrastructure.database import (
    create_session_factory,
    protocol_versions_table,
    sessions_table,
)
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


@pytest.fixture()
def api(tmp_path):
    settings = Settings(
        app_env="test",
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{tmp_path / 'atomic-approval.db'}",
        retain_transcripts=False,
    )
    with TestClient(create_app(settings, create_schema=True)) as client:
        yield client, settings


def _import(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/protocols/upload",
        files={"file": ("development-only.docx", sample_protocol(), DOCX_MIME)},
    )
    assert response.status_code == 201
    return response.json()["version"]


def _counts(settings: Settings) -> tuple[int, int]:
    """(approved versions, sessions) read straight from the database."""

    factory = create_session_factory(settings.database_url, create_schema=True)
    with factory() as connection:
        approved = connection.execute(
            select(func.count())
            .select_from(protocol_versions_table)
            .where(protocol_versions_table.c.status == "APPROVED")
        ).scalar_one()
        sessions = connection.execute(
            select(func.count()).select_from(sessions_table)
        ).scalar_one()
    return int(approved), int(sessions)


def test_review_approve_and_start_commits_once(api) -> None:
    client, settings = api
    version = _import(client)

    response = client.post(
        f"/api/v1/protocol-versions/{version['id']}/review-approve-and-start",
        headers={"If-Match": '"0"', "Idempotency-Key": "approve-1"},
        json={"steps": version["steps"]},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["version"]["status"] == "APPROVED"
    # Review bumps the revision once and approval bumps it again, in one commit.
    assert body["version"]["revision"] == 2
    assert body["session"]["state"] == "READY"
    assert _counts(settings) == (1, 1)


def test_review_approve_and_start_records_a_normalized_operator_label(api) -> None:
    client, _ = api
    version = _import(client)

    response = client.post(
        f"/api/v1/protocol-versions/{version['id']}/review-approve-and-start",
        headers={"If-Match": '"0"', "Idempotency-Key": "approve-labelled"},
        json={"steps": version["steps"], "label": "  batch   7, replicate A  "},
    )

    assert response.status_code == 201
    assert response.json()["session"]["label"] == "batch 7, replicate A"


def test_same_key_with_a_different_label_is_refused(api) -> None:
    client, settings = api
    version = _import(client)
    headers = {"If-Match": '"0"', "Idempotency-Key": "approve-label-conflict"}

    first = client.post(
        f"/api/v1/protocol-versions/{version['id']}/review-approve-and-start",
        headers=headers,
        json={"steps": version["steps"], "label": "batch A"},
    )
    replayed = client.post(
        f"/api/v1/protocol-versions/{version['id']}/review-approve-and-start",
        headers=headers,
        json={"steps": version["steps"], "label": "batch B"},
    )

    assert first.status_code == 201
    assert replayed.status_code == 409
    assert replayed.json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert _counts(settings) == (1, 1)


def test_atomic_approval_reports_the_stable_label_limit_error(api) -> None:
    client, settings = api
    version = _import(client)

    response = client.post(
        f"/api/v1/protocol-versions/{version['id']}/review-approve-and-start",
        headers={"If-Match": '"0"', "Idempotency-Key": "approve-long-label"},
        json={"steps": version["steps"], "label": "x" * 121},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "SESSION_LABEL_TOO_LONG"
    assert _counts(settings) == (0, 0)


def test_retry_with_the_same_key_reports_the_original_outcome(api) -> None:
    """A lost response must not approve twice or open a second session."""

    client, settings = api
    version = _import(client)
    payload = {"steps": version["steps"]}
    headers = {"If-Match": '"0"', "Idempotency-Key": "approve-retry"}

    first = client.post(
        f"/api/v1/protocol-versions/{version['id']}/review-approve-and-start",
        headers=headers,
        json=payload,
    )
    second = client.post(
        f"/api/v1/protocol-versions/{version['id']}/review-approve-and-start",
        headers=headers,
        json=payload,
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["session"]["id"] == first.json()["session"]["id"]
    assert second.json()["version"]["revision"] == first.json()["version"]["revision"]
    assert _counts(settings) == (1, 1)


def test_same_key_with_different_steps_is_refused(api) -> None:
    client, settings = api
    version = _import(client)
    headers = {"If-Match": '"0"', "Idempotency-Key": "approve-conflict"}

    client.post(
        f"/api/v1/protocol-versions/{version['id']}/review-approve-and-start",
        headers=headers,
        json={"steps": version["steps"]},
    )
    edited = [dict(step) for step in version["steps"]]
    edited[0]["instruction"] = "A different reviewed instruction entirely."
    replayed = client.post(
        f"/api/v1/protocol-versions/{version['id']}/review-approve-and-start",
        headers=headers,
        json={"steps": edited},
    )

    assert replayed.status_code == 409
    assert replayed.json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert _counts(settings) == (1, 1)


def test_a_stale_revision_changes_nothing(api) -> None:
    client, settings = api
    version = _import(client)

    response = client.post(
        f"/api/v1/protocol-versions/{version['id']}/review-approve-and-start",
        headers={"If-Match": '"7"', "Idempotency-Key": "approve-stale"},
        json={"steps": version["steps"]},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "PROTOCOL_VERSION_CONFLICT"
    assert _counts(settings) == (0, 0)


def test_invalid_steps_leave_no_approved_version_or_session(api) -> None:
    """Failure inside the transaction must not persist the reviewed revision."""

    client, settings = api
    version = _import(client)
    blank = [dict(step) for step in version["steps"]]
    blank[0]["instruction"] = "   "

    response = client.post(
        f"/api/v1/protocol-versions/{version['id']}/review-approve-and-start",
        headers={"If-Match": '"0"', "Idempotency-Key": "approve-invalid"},
        json={"steps": blank},
    )

    assert response.status_code in {400, 422}
    assert _counts(settings) == (0, 0)
    # The draft is untouched, so the browser's revision number is still correct.
    reread = client.get(f"/api/v1/protocol-versions/{version['id']}")
    assert reread.json()["revision"] == 0
    assert reread.json()["status"] == "DRAFT"


def test_an_already_approved_version_cannot_be_approved_again(api) -> None:
    client, settings = api
    version = _import(client)
    client.post(
        f"/api/v1/protocol-versions/{version['id']}/review-approve-and-start",
        headers={"If-Match": '"0"', "Idempotency-Key": "approve-once"},
        json={"steps": version["steps"]},
    )

    again = client.post(
        f"/api/v1/protocol-versions/{version['id']}/review-approve-and-start",
        headers={"If-Match": '"2"', "Idempotency-Key": "approve-twice"},
        json={"steps": version["steps"]},
    )

    assert again.status_code == 409
    assert _counts(settings) == (1, 1)


def test_sessions_can_be_listed_by_protocol_version(api) -> None:
    """The approved-protocol screen needs the session for the version in its URL."""

    client, _ = api
    version = _import(client)
    started = client.post(
        f"/api/v1/protocol-versions/{version['id']}/review-approve-and-start",
        headers={"If-Match": '"0"', "Idempotency-Key": "approve-listing"},
        json={"steps": version["steps"]},
    ).json()

    matching = client.get(
        "/api/v1/sessions",
        params={"protocol_version_id": version["id"]},
    ).json()["sessions"]
    unrelated = client.get(
        "/api/v1/sessions",
        params={"protocol_version_id": "not-a-real-version"},
    ).json()["sessions"]

    assert [item["session_id"] for item in matching] == [started["session"]["id"]]
    assert unrelated == []
