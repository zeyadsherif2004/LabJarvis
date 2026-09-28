"""Protocol questions are answered from the approved document, or refused.

Exercised through the same gateway everything else uses, so the guarantee holds
for a voice question and a typed one identically.
"""

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


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        app_env="test",
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'grounded.db').as_posix()}",
    )
    with TestClient(create_app(settings, create_schema=True)) as test_client:
        yield test_client


def _start(client: TestClient) -> tuple[str, int]:
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
    body = client.post(
        f"/api/v1/protocol-versions/{version_id}/approve-and-start",
        headers={"If-Match": f'"{reviewed["revision"]}"'},
    ).json()
    session_id = body["session"]["id"]
    started = client.post(
        f"/api/v1/sessions/{session_id}/start",
        headers={"If-Match": f'"{body["session"]["version"]}"'},
    ).json()
    return session_id, int(started["version"])


def _ask(client: TestClient, session_id: str, version: int, question: str, key: str) -> dict:
    response = client.post(
        f"/api/v1/sessions/{session_id}/tools/answer_protocol_question",
        headers={"If-Match": f'"{version}"', "Idempotency-Key": key},
        json={"parameters": {"question": question}},
    )
    assert response.status_code == 200, response.text
    result: dict = response.json()
    return result


def test_an_answerable_question_quotes_the_protocol_with_its_location(client) -> None:
    session_id, version = _start(client)

    result = _ask(client, session_id, version, "how much ethanol do I add", "q-1")

    assert result["data"]["grounded"] == "true"
    assert result["data"]["citation"]
    assert result["data"]["source_page"]
    assert "ethanol" in result["message"].casefold()
    # Reading the protocol is never a state change.
    assert result["committed"] is False
    assert result["pending_action"] is None


def test_a_question_the_protocol_does_not_address_is_refused_not_invented(client) -> None:
    session_id, version = _start(client)

    result = _ask(
        client,
        session_id,
        version,
        "what is the maximum permitted autoclave pressure",
        "q-2",
    )

    assert result["data"]["abstained"] == "true"
    assert "not specified in the approved protocol" in result["message"]


def test_answering_a_question_never_consults_a_language_model(client) -> None:
    session_id, version = _start(client)

    _ask(client, session_id, version, "how much ethanol do I add", "q-3")

    inferences = client.get(f"/api/v1/sessions/{session_id}/notebook").json()["inferences"]
    assert inferences == []


def test_the_answer_is_recorded_in_the_audit_trail(client) -> None:
    session_id, version = _start(client)

    _ask(client, session_id, version, "how much ethanol do I add", "q-4")

    events = client.get(f"/api/v1/sessions/{session_id}/notebook").json()["audit_events"]
    assert any(item["event_type"] == "tool_answered_protocol_question" for item in events)


def test_a_blank_question_is_rejected_by_the_schema(client) -> None:
    session_id, version = _start(client)

    response = client.post(
        f"/api/v1/sessions/{session_id}/tools/answer_protocol_question",
        headers={"If-Match": f'"{version}"', "Idempotency-Key": "q-5"},
        json={"parameters": {}},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "TOOL_PARAMETERS_INVALID"


def test_the_question_tool_is_published_as_read_only(client) -> None:
    tools = client.get("/api/v1/tools").json()["tools"]

    spec = next(item for item in tools if item["name"] == "answer_protocol_question")
    assert spec["kind"] == "read_only"
    assert spec["confirmation"] == "none"
    # Reading the protocol aloud is exactly what hands-free operation is for.
    assert spec["realtime_exposed"] is True
