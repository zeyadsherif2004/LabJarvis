import fitz
from fastapi.testclient import TestClient
from sqlalchemy import select

from labjarvis.api.app import create_app
from labjarvis.infrastructure.database import model_inferences_table
from labjarvis.settings import Settings


def _blank_pdf() -> bytes:
    document = fitz.open()
    document.new_page()
    data = document.tobytes()
    document.close()
    return data


def test_problem_details_has_stable_code_and_correlation_id(tmp_path) -> None:
    settings = Settings(
        app_env="test",
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{tmp_path / 'api-errors.db'}",
    )
    client = TestClient(create_app(settings, create_schema=True))

    response = client.post(
        "/api/v1/protocols/upload",
        headers={"X-Correlation-ID": "test-correlation"},
        files={"file": ("x.exe", b"MZ", "application/octet-stream")},
    )

    assert response.status_code == 415
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["X-Correlation-ID"] == "test-correlation"
    assert response.json()["code"] == "UNSUPPORTED_DOCUMENT"
    assert response.json()["correlation_id"] == "test-correlation"
    assert "traceback" not in response.text.casefold()
    assert response.headers["Content-Security-Policy"] == (
        "default-src 'none'; frame-ancestors 'none'"
    )
    assert response.headers["Permissions-Policy"] == (
        "camera=(), microphone=(), geolocation=()"
    )


def test_mutation_preconditions_are_required_before_route_logic(tmp_path) -> None:
    settings = Settings(
        app_env="test",
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{tmp_path / 'preconditions.db'}",
    )
    client = TestClient(create_app(settings, create_schema=True))

    missing_version = client.post("/api/v1/sessions/missing/start")
    missing_idempotency = client.post(
        "/api/v1/sessions/missing/commands/text",
        headers={"If-Match": '"0"'},
        json={"transcript": "pause"},
    )

    assert missing_version.status_code == 428
    assert missing_version.json()["code"] == "IF_MATCH_REQUIRED"
    assert missing_idempotency.status_code == 428
    assert missing_idempotency.json()["code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_oversized_upload_is_rejected(tmp_path) -> None:
    settings = Settings(
        app_env="test",
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{tmp_path / 'too-large.db'}",
        max_document_bytes=1024,
    )
    client = TestClient(create_app(settings, create_schema=True))

    response = client.post(
        "/api/v1/protocols/upload",
        files={"file": ("large.pdf", b"%PDF-" + b"x" * 2048, "application/pdf")},
    )

    assert response.status_code == 413
    assert response.json()["code"] == "DOCUMENT_TOO_LARGE"


def test_scanned_pdf_requires_explicit_ocr_and_fake_profile_reports_unavailable(
    tmp_path,
) -> None:
    settings = Settings(
        app_env="test",
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{tmp_path / 'scanned.db'}",
    )
    client = TestClient(create_app(settings, create_schema=True))
    scanned = _blank_pdf()

    initial = client.post(
        "/api/v1/protocols/upload",
        files={"file": ("scan.pdf", scanned, "application/pdf")},
    )
    explicit_retry = client.post(
        "/api/v1/protocols/upload",
        params={"ocr": True},
        files={"file": ("scan.pdf", scanned, "application/pdf")},
    )

    assert initial.status_code == 422
    assert initial.json()["code"] == "DOCUMENT_OCR_REQUIRED"
    assert explicit_retry.status_code == 422
    assert explicit_retry.json()["code"] == "DOCUMENT_OCR_UNAVAILABLE"
    with client.app.state.session_factory() as database:
        failure = database.execute(
            select(model_inferences_table).where(
                model_inferences_table.c.modality == "document_ocr"
            )
        ).mappings().one()
    assert failure["session_id"] is None
    assert failure["status"] == "failed"
    assert "DOCUMENT_OCR_UNAVAILABLE" in failure["output_json"]


def test_structured_logs_include_safe_error_metadata_but_omit_transcripts(
    tmp_path,
    capsys,
) -> None:
    settings = Settings(
        app_env="test",
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{tmp_path / 'privacy-logs.db'}",
        log_format="json",
    )
    client = TestClient(create_app(settings, create_schema=True))
    capsys.readouterr()

    secret_transcript = "PRIVATE-PROTOCOL-VALUE-DO-NOT-LOG"
    response = client.post(
        "/api/v1/sessions/missing/commands/text",
        headers={"If-Match": '"0"', "Idempotency-Key": "privacy-test"},
        json={"transcript": secret_transcript},
    )
    output = capsys.readouterr().out

    assert response.status_code == 404
    assert "SESSION_NOT_FOUND" in output
    assert "correlation_id" in output
    assert secret_transcript not in output
