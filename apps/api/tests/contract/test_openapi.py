from fastapi.testclient import TestClient

from labjarvis.api.app import create_app
from labjarvis.infrastructure.media_storage import (
    DiscardRawMediaStore,
    LocalRawMediaStore,
)
from labjarvis.settings import Settings


def test_openapi_exposes_versioned_workflow_and_strict_problem_schema(tmp_path) -> None:
    settings = Settings(
        app_env="test",
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{tmp_path / 'contract.db'}",
    )
    app = create_app(settings, create_schema=True)
    client = TestClient(app)

    schema = client.get("/openapi.json").json()

    required_paths = {
        "/api/v1/protocols/upload",
        "/api/v1/protocols/{protocol_id}",
        "/api/v1/protocol-versions/{version_id}",
        "/api/v1/protocol-versions/{version_id}/approve",
        "/api/v1/sessions",
        "/api/v1/sessions/{session_id}/start",
        "/api/v1/sessions/{session_id}/commands/text",
        "/api/v1/pending-actions/{action_id}/confirm",
        "/api/v1/sessions/{session_id}/reagent-verifications",
        "/api/v1/sessions/{session_id}/notebook",
    }
    assert set(schema["paths"]) >= required_paths
    assert "get" in schema["paths"]["/api/v1/protocol-versions/{version_id}"]
    problem = schema["components"]["schemas"]["ProblemDetail"]
    assert set(problem["required"]) >= {"status", "detail", "code", "correlation_id"}


def test_health_liveness_and_readiness_are_distinct(tmp_path) -> None:
    settings = Settings(
        app_env="test",
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{tmp_path / 'health.db'}",
    )
    app = create_app(settings, create_schema=True)
    client = TestClient(app)

    live = client.get("/health/live")
    ready = client.get("/health/ready")

    assert live.status_code == 200
    assert live.json() == {"status": "alive"}
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert ready.json()["mode"] == "fake"
    assert ready.json()["interaction"]["wake_phrase"] == "Jarvis"
    assert ready.json()["adapters"]["speech"]["ready"] is True
    assert ready.json()["adapters"]["speech"]["adapter"] == "deterministic-fake-speech"
    assert ready.json()["adapters"]["text"]["model_name"] == "fake-text-rules-v1"
    assert ready.json()["privacy"]["raw_media_retention"] is False
    assert ready.json()["privacy"]["transcript_retention"] is True
    assert isinstance(app.state.raw_media_store, DiscardRawMediaStore)


def test_explicit_raw_media_retention_is_visible_in_readiness(tmp_path) -> None:
    settings = Settings(
        app_env="test",
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{tmp_path / 'retention.db'}",
        retain_raw_media=True,
    )
    app = create_app(settings, create_schema=True)
    client = TestClient(app)

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["privacy"]["raw_media_retention"] is True
    assert isinstance(app.state.raw_media_store, LocalRawMediaStore)


def test_untrusted_host_header_is_rejected(tmp_path) -> None:
    settings = Settings(
        app_env="test",
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{tmp_path / 'hosts.db'}",
    )
    client = TestClient(create_app(settings, create_schema=True))

    response = client.get("/health/live", headers={"Host": "attacker.example"})

    assert response.status_code == 400
