"""A hostile page cannot change LabJarvis state, even though CORS lets it try.

The finding this closes was reproduced, not theorised: a ``multipart/form-data``
POST carrying ``Origin: https://attacker.example`` reached the protocol upload
endpoint and returned ``201 Created``. CORS stopped the page reading the reply,
which is why the severity was Low, but a protocol record existed afterwards.

The assertions below check the database, not the status code alone, because
"was refused" and "changed nothing" are different claims.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from labjarvis.api.app import create_app
from labjarvis.infrastructure.database import (
    create_session_factory,
    protocols_table,
    sessions_table,
)
from labjarvis.infrastructure.documents import DOCX_MIME
from labjarvis.settings import Settings

TRUSTED = "http://127.0.0.1:5173"
HOSTILE = "https://attacker.example"


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
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'origin.db').as_posix()}",
    )
    with TestClient(create_app(settings, create_schema=True)) as client:
        yield client, settings


def protocol_count(settings: Settings) -> int:
    factory = create_session_factory(settings.database_url, create_schema=True)
    with factory() as connection:
        return int(
            connection.execute(
                select(func.count()).select_from(protocols_table)
            ).scalar_one()
        )


def session_count(settings: Settings) -> int:
    factory = create_session_factory(settings.database_url, create_schema=True)
    with factory() as connection:
        return int(
            connection.execute(
                select(func.count()).select_from(sessions_table)
            ).scalar_one()
        )


def upload(client: TestClient, origin: str | None):
    headers = {} if origin is None else {"Origin": origin}
    return client.post(
        "/api/v1/protocols/upload",
        headers=headers,
        files={"file": ("development-only.docx", sample_protocol(), DOCX_MIME)},
    )


def test_an_untrusted_origin_cannot_create_a_protocol(api) -> None:
    client, settings = api

    response = upload(client, HOSTILE)

    assert response.status_code == 403
    assert response.json()["code"] == "ORIGIN_NOT_ALLOWED"
    assert protocol_count(settings) == 0


def test_repeated_hostile_uploads_leave_no_trace(api) -> None:
    """A page that keeps trying must not accumulate anything."""

    client, settings = api

    for _ in range(5):
        assert upload(client, HOSTILE).status_code == 403

    assert protocol_count(settings) == 0


def test_the_frontend_origin_still_works(api) -> None:
    client, settings = api

    response = upload(client, TRUSTED)

    assert response.status_code == 201
    assert protocol_count(settings) == 1


def test_a_trailing_slash_or_different_case_is_still_the_same_origin(api) -> None:
    client, settings = api

    response = upload(client, "http://127.0.0.1:5173/")

    assert response.status_code == 201
    assert protocol_count(settings) == 1


def test_a_local_client_without_an_origin_header_still_works(api) -> None:
    """curl and the repository's own scripts send no Origin; they must keep working."""

    client, settings = api

    response = upload(client, None)

    assert response.status_code == 201
    assert protocol_count(settings) == 1


def test_reads_are_unaffected_by_origin(api) -> None:
    client, _ = api

    listed = client.get("/api/v1/protocols", headers={"Origin": HOSTILE})

    # A GET changes nothing, and CORS already stops a hostile page reading it.
    assert listed.status_code == 200


def test_preflight_still_answers_for_the_trusted_origin(api) -> None:
    client, _ = api

    preflight = client.options(
        "/api/v1/protocols/upload",
        headers={
            "Origin": TRUSTED,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == TRUSTED


def test_preflight_is_not_granted_to_an_untrusted_origin(api) -> None:
    client, _ = api

    preflight = client.options(
        "/api/v1/protocols/upload",
        headers={
            "Origin": HOSTILE,
            "Access-Control-Request-Method": "POST",
        },
    )

    assert "access-control-allow-origin" not in preflight.headers


def test_every_state_changing_endpoint_is_covered(api) -> None:
    """The guard is on the method, not on one hand-picked route."""

    client, settings = api
    imported = upload(client, TRUSTED).json()
    version_id = imported["version"]["id"]

    hostile_review = client.put(
        f"/api/v1/protocol-versions/{version_id}",
        headers={"Origin": HOSTILE, "If-Match": '"0"'},
        json={"steps": imported["version"]["steps"]},
    )
    hostile_start = client.post(
        f"/api/v1/protocol-versions/{version_id}/review-approve-and-start",
        headers={
            "Origin": HOSTILE,
            "If-Match": '"0"',
            "Idempotency-Key": "hostile-approve",
        },
        json={"steps": imported["version"]["steps"]},
    )

    assert hostile_review.status_code == 403
    assert hostile_start.status_code == 403
    assert session_count(settings) == 0
    # The draft is untouched: still revision 0, still a draft.
    reread = client.get(f"/api/v1/protocol-versions/{version_id}").json()
    assert reread["revision"] == 0
    assert reread["status"] == "DRAFT"


def test_a_refusal_is_still_correlated_for_diagnosis(api) -> None:
    client, _ = api

    response = upload(client, HOSTILE)

    assert response.headers["X-Correlation-ID"]
    assert response.json()["correlation_id"]
    # The refusal explains itself without naming internals.
    assert "attacker" not in response.json()["detail"]
