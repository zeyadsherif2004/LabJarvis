import json
from hashlib import sha256
from pathlib import Path

from fastapi.testclient import TestClient

from labjarvis.api.app import create_app
from labjarvis.infrastructure.documents import DOCX_MIME
from labjarvis.settings import Settings


def _sample_protocol() -> bytes:
    return (
        Path(__file__).parents[4]
        / "sample_data"
        / "protocols"
        / "development-only-ethanol-protocol.docx"
    ).read_bytes()


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def test_live_backup_manifest_and_staged_restore_never_replace_active_database(
    tmp_path,
) -> None:
    database_path = tmp_path / "active.db"
    canary = "backup-secret-canary-value"
    settings = Settings(
        app_env="test",
        data_dir=tmp_path,
        backup_dir=tmp_path / "backups",
        database_url=f"sqlite+pysqlite:///{database_path}",
        hosted_api_key=canary,
        application_commit="backup-test-commit",
    )
    with TestClient(create_app(settings, create_schema=True)) as client:
        imported = client.post(
            "/api/v1/protocols/upload",
            files={"file": ("protocol.docx", _sample_protocol(), DOCX_MIME)},
        )
        assert imported.status_code == 201

        created = client.post("/api/v1/data/backups")
        assert created.status_code == 201
        body = created.json()
        package = settings.backup_dir / body["backup_name"]
        manifest_path = package / "manifest.json"
        backed_up_database = package / "database.sqlite3"
        assert manifest_path.is_file()
        assert backed_up_database.is_file()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["application_commit"] == "backup-test-commit"
        assert manifest["profile"] == "fake"
        assert manifest["files"] == [
            {
                "path": "database.sqlite3",
                "sha256": _digest(backed_up_database),
                "size": backed_up_database.stat().st_size,
            }
        ]
        assert canary not in manifest_path.read_text(encoding="utf-8")
        assert canary.encode() not in backed_up_database.read_bytes()

        active_before = _digest(database_path)
        validated = client.post(f"/api/v1/data/backups/{body['backup_name']}/validate")
        assert validated.status_code == 200
        validation = validated.json()
        assert validation["activation_required"] is True
        staged = tmp_path / "restore-staging" / validation["staging_name"]
        assert staged.is_file()
        assert _digest(staged) == _digest(backed_up_database)
        assert _digest(database_path) == active_before


def test_tampered_backup_is_rejected_before_staging(tmp_path) -> None:
    settings = Settings(
        app_env="test",
        data_dir=tmp_path,
        backup_dir=tmp_path / "backups",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'active.db'}",
    )
    with TestClient(create_app(settings, create_schema=True)) as client:
        created = client.post("/api/v1/data/backups")
        assert created.status_code == 201
        backup_name = created.json()["backup_name"]
        package = settings.backup_dir / backup_name
        database_backup = package / "database.sqlite3"
        database_backup.write_bytes(database_backup.read_bytes() + b"tampered")

        response = client.post(f"/api/v1/data/backups/{backup_name}/validate")
        assert response.status_code == 422
        assert response.json()["code"] == "BACKUP_CHECKSUM_MISMATCH"
        assert not (tmp_path / "restore-staging" / f"{backup_name}.sqlite3").exists()
