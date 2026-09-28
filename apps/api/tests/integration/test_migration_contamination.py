"""Migrations must reconcile a database that runtime schema creation contaminated.

``create_app()`` used to call ``metadata.create_all()`` on every start. Against a
database stamped at an older revision that silently created *current* tables
ahead of the migrations that own them, so the next ``alembic upgrade head``
died on ``table session_timers already exists`` and the database could not move
forward at all.

Two contaminated shapes matter, and they are different:

* **Current-metadata contamination** - what today's ``create_all()`` produces:
  ``utterances`` already carries the columns migration ``0006`` adds.
* **Historical contamination** - what the developer workstation actually holds:
  ``create_all()`` ran when the metadata was still at the ``0005`` shape, so
  ``utterances`` exists *without* the ``0006`` columns.

Both must upgrade to head, apply every column, index, and backfill that was
missed, and preserve every existing row. Neither may be repaired by stamping.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

REPOSITORY_ROOT = Path(__file__).parents[4]

# The revision the contaminated developer database is stamped at.
CONTAMINATED_REVISION = "0003_verification_review"
HEAD_REVISION = "0010_protocol_import_review"

EXPECTED_TABLES = {
    "alembic_version",
    "audit_events",
    "idempotency_records",
    "model_inferences",
    "notebook_entries",
    "pending_actions",
    "protocol_versions",
    "protocols",
    "reagent_verifications",
    "session_timers",
    "sessions",
    "utterances",
}

# Exactly what migration 0004 creates, so a pre-created table can be recognised
# as compatible rather than blindly reused.
SESSION_TIMERS_BASE_DDL = """
CREATE TABLE session_timers (
    id VARCHAR(36) NOT NULL,
    session_id VARCHAR(36) NOT NULL,
    step_id VARCHAR(80),
    label VARCHAR(120) NOT NULL,
    duration_seconds INTEGER NOT NULL,
    state VARCHAR(20) NOT NULL,
    created_at DATETIME NOT NULL,
    started_at DATETIME NOT NULL,
    deadline_at DATETIME,
    remaining_seconds_at_pause INTEGER,
    paused_at DATETIME,
    resolved_at DATETIME,
    correlation_id VARCHAR(80) NOT NULL,
    version INTEGER NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE
)
"""

# The 0005-era shape: no purpose/pending_action_id/audio_sha256/decision.
UTTERANCES_HISTORICAL_DDL = """
CREATE TABLE utterances (
    id VARCHAR(36) NOT NULL,
    session_id VARCHAR(36) NOT NULL,
    realtime_transcript TEXT NOT NULL,
    verification_transcript TEXT NOT NULL,
    canonical_transcript TEXT NOT NULL,
    agreement VARCHAR(20) NOT NULL,
    reasons_json TEXT NOT NULL,
    primary_model VARCHAR(160) NOT NULL,
    verification_model VARCHAR(160) NOT NULL,
    primary_inference_id VARCHAR(36) NOT NULL,
    verification_inference_id VARCHAR(36) NOT NULL,
    confirmed_transcript TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    correlation_id VARCHAR(80) NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE
)
"""


def alembic_config(database_url: str) -> Config:
    config = Config(str(REPOSITORY_ROOT / "apps" / "api" / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.fixture(autouse=True)
def _isolate_from_developer_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """``migrations/env.py`` prefers the environment variable over the config."""

    monkeypatch.delenv("LABJARVIS_DATABASE_URL", raising=False)


def sqlite_url(path: Path) -> str:
    return f"sqlite+pysqlite:///{path.as_posix()}"


# --------------------------------------------------------------------- seeding

STEPS_WITHOUT_REQUIREMENT_IDS = [
    {
        "id": "step-1",
        "title": "Dissolve the reagent",
        "requirements": [
            {"label": "Sodium chloride", "quantity": "5 g"},
            {"label": "Deionised water", "quantity": "100 mL"},
        ],
    },
    {
        "id": "step-2",
        "title": "Measure the volume",
        "requirements": [{"label": "Graduated cylinder", "quantity": "1"}],
    },
]


def seed_pre_migration_data(engine: sa.Engine) -> None:
    """Write the rows a real workstation database already holds at 0003."""

    now = datetime(2026, 7, 16, 12, 0, tzinfo=UTC).replace(tzinfo=None)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO protocols (id, title, created_at) "
                "VALUES (:id, :title, :created)"
            ),
            {"id": "protocol-1", "title": "Buffer preparation", "created": now},
        )
        connection.execute(
            text(
                "INSERT INTO protocol_versions "
                "(id, protocol_id, version_number, revision, status, steps_json, "
                " source_sha256, created_at, approved_at, source_text) "
                "VALUES (:id, :protocol_id, 1, 1, 'APPROVED', :steps, :sha, "
                " :created, :approved, :source)"
            ),
            {
                "id": "version-1",
                "protocol_id": "protocol-1",
                "steps": json.dumps(STEPS_WITHOUT_REQUIREMENT_IDS),
                "sha": "a" * 64,
                "created": now,
                "approved": now,
                "source": "Dissolve the reagent. Measure the volume.",
            },
        )
        connection.execute(
            text(
                "INSERT INTO sessions "
                "(id, protocol_version_id, protocol_approved, state, steps_json, "
                " active_step_id, version, created_at, started_at, ended_at) "
                "VALUES (:id, :version_id, :protocol_approved, 'ACTIVE', :steps, "
                " 'step-1', 4, "
                " :created, :created, NULL)"
            ),
            {
                "id": "session-1",
                "version_id": "version-1",
                "protocol_approved": True,
                "steps": json.dumps(STEPS_WITHOUT_REQUIREMENT_IDS),
                "created": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO notebook_entries "
                "(id, session_id, entry_type, source, payload_json, created_at, "
                " correlation_id, supersedes_entry_id, model_inference_id) "
                "VALUES (:id, :session_id, 'MEASUREMENT', 'OPERATOR', :payload, "
                " :created, 'corr-1', NULL, NULL)"
            ),
            {
                "id": "entry-1",
                "session_id": "session-1",
                "payload": json.dumps({"kind": "volume", "value": "10", "unit": "mL"}),
                "created": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO reagent_verifications "
                "(id, session_id, step_id, expected_label, observed_label, outcome, "
                " score, model_inference_id, created_at, expected_normalised, "
                " observed_normalised, match_threshold, uncertain_threshold, "
                " model_confidence, decision, resolved_at) "
                "VALUES (:id, :session_id, 'step-1', 'Sodium chloride', "
                " 'Sodium chloride', 'MATCH', 0.98, NULL, :created, "
                " 'sodium chloride', 'sodium chloride', 0.8, 0.6, 0.98, "
                " 'ACCEPTED', :created)"
            ),
            {"id": "verification-1", "session_id": "session-1", "created": now},
        )


def contaminate(engine: sa.Engine, *, historical: bool) -> None:
    """Reproduce what runtime ``metadata.create_all()`` left behind.

    ``historical=True`` reproduces the developer workstation exactly: the
    metadata was at the 0005 shape when it ran, so ``utterances`` lacks the
    columns 0006 adds. ``historical=False`` reproduces what today's metadata
    would create, where those columns are already present.
    """

    from labjarvis.infrastructure.database import metadata

    with engine.begin() as connection:
        connection.execute(text(SESSION_TIMERS_BASE_DDL))
        connection.execute(
            text(
                "CREATE INDEX ix_session_timers_session_state "
                "ON session_timers (session_id, state)"
            )
        )
        if historical:
            connection.execute(text(UTTERANCES_HISTORICAL_DDL))
        else:
            metadata.tables["utterances"].create(connection)
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_utterances_session_created "
                "ON utterances (session_id, created_at)"
            )
        )


# ------------------------------------------------------------------ assertions


def assert_at_head(engine: sa.Engine) -> None:
    with engine.connect() as connection:
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    assert revision == HEAD_REVISION


def assert_schema_is_complete(engine: sa.Engine) -> None:
    from labjarvis.infrastructure.database import metadata

    inspector = inspect(engine)
    assert set(inspector.get_table_names()) >= EXPECTED_TABLES

    for table in metadata.sorted_tables:
        migrated = {column["name"] for column in inspector.get_columns(table.name)}
        assert migrated == set(table.c.keys()), f"{table.name} columns diverged"

    timer_indexes = {index["name"] for index in inspector.get_indexes("session_timers")}
    assert "ix_session_timers_session_state" in timer_indexes
    utterance_indexes = {index["name"] for index in inspector.get_indexes("utterances")}
    assert "ix_utterances_session_created" in utterance_indexes
    # Added by 0006 and looked up on every spoken confirmation.
    assert "ix_utterances_session_audio" in utterance_indexes


def assert_requirement_ids_backfilled(engine: sa.Engine) -> None:
    with engine.connect() as connection:
        steps_json = connection.execute(
            text("SELECT steps_json FROM protocol_versions WHERE id = 'version-1'")
        ).scalar_one()
    steps = json.loads(steps_json)
    identifiers = [
        requirement["requirement_id"]
        for step in steps
        for requirement in step["requirements"]
    ]
    assert identifiers == ["step-1-req-1", "step-1-req-2", "step-2-req-1"]


def assert_seeded_rows_survived(engine: sa.Engine) -> None:
    with engine.connect() as connection:
        counts = {
            table: connection.execute(
                text(f"SELECT COUNT(*) FROM {table}")
            ).scalar_one()
            for table in (
                "protocols",
                "protocol_versions",
                "sessions",
                "notebook_entries",
                "reagent_verifications",
            )
        }
        assert counts == {
            "protocols": 1,
            "protocol_versions": 1,
            "sessions": 1,
            "notebook_entries": 1,
            "reagent_verifications": 1,
        }
        # Representative records, not just cardinality.
        assert (
            connection.execute(
                text("SELECT title FROM protocols WHERE id = 'protocol-1'")
            ).scalar_one()
            == "Buffer preparation"
        )
        assert (
            connection.execute(
                text("SELECT state FROM sessions WHERE id = 'session-1'")
            ).scalar_one()
            == "ACTIVE"
        )
        assert (
            connection.execute(
                text("SELECT outcome FROM reagent_verifications WHERE id = :id"),
                {"id": "verification-1"},
            ).scalar_one()
            == "MATCH"
        )
        # 0004 adds this column with a server default; existing rows keep it.
        assert (
            connection.execute(
                text("SELECT requirement_id FROM reagent_verifications WHERE id = :id"),
                {"id": "verification-1"},
            ).scalar_one()
            == ""
        )


# ----------------------------------------------------------------- clean paths


def test_empty_sqlite_migrates_straight_to_head(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "clean.db")

    command.upgrade(alembic_config(url), "head")

    engine = create_engine(url)
    assert_at_head(engine)
    assert_schema_is_complete(engine)


def test_empty_sqlite_migrates_through_0003_to_head(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "staged.db")
    config = alembic_config(url)

    command.upgrade(config, CONTAMINATED_REVISION)
    command.upgrade(config, "head")

    engine = create_engine(url)
    assert_at_head(engine)
    assert_schema_is_complete(engine)


def test_seeded_0003_database_backfills_requirement_ids(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "seeded.db")
    config = alembic_config(url)
    command.upgrade(config, CONTAMINATED_REVISION)
    engine = create_engine(url)
    seed_pre_migration_data(engine)

    command.upgrade(config, "head")

    assert_at_head(engine)
    assert_schema_is_complete(engine)
    assert_requirement_ids_backfilled(engine)
    assert_seeded_rows_survived(engine)


# ---------------------------------------------------------- contaminated paths


@pytest.mark.parametrize("historical", [False, True], ids=["current", "historical"])
def test_contaminated_0003_database_still_migrates_to_head(
    tmp_path: Path, historical: bool
) -> None:
    """The regression. Before the fix this died on ``table already exists``."""

    url = sqlite_url(tmp_path / "contaminated.db")
    config = alembic_config(url)
    command.upgrade(config, CONTAMINATED_REVISION)
    engine = create_engine(url)
    seed_pre_migration_data(engine)
    contaminate(engine, historical=historical)

    command.upgrade(config, "head")

    assert_at_head(engine)
    assert_schema_is_complete(engine)


@pytest.mark.parametrize("historical", [False, True], ids=["current", "historical"])
def test_contaminated_migration_preserves_every_existing_row(
    tmp_path: Path, historical: bool
) -> None:
    url = sqlite_url(tmp_path / "contaminated-data.db")
    config = alembic_config(url)
    command.upgrade(config, CONTAMINATED_REVISION)
    engine = create_engine(url)
    seed_pre_migration_data(engine)
    contaminate(engine, historical=historical)

    command.upgrade(config, "head")

    assert_seeded_rows_survived(engine)


@pytest.mark.parametrize("historical", [False, True], ids=["current", "historical"])
def test_contaminated_migration_still_runs_the_json_backfill(
    tmp_path: Path, historical: bool
) -> None:
    """A pre-created table must not cause a migration's side effects to be skipped."""

    url = sqlite_url(tmp_path / "contaminated-backfill.db")
    config = alembic_config(url)
    command.upgrade(config, CONTAMINATED_REVISION)
    engine = create_engine(url)
    seed_pre_migration_data(engine)
    contaminate(engine, historical=historical)

    command.upgrade(config, "head")

    assert_requirement_ids_backfilled(engine)


def test_reused_table_keeps_rows_written_before_the_upgrade(tmp_path: Path) -> None:
    """Reuse means reuse: a row already in the pre-created table survives."""

    url = sqlite_url(tmp_path / "contaminated-rows.db")
    config = alembic_config(url)
    command.upgrade(config, CONTAMINATED_REVISION)
    engine = create_engine(url)
    seed_pre_migration_data(engine)
    contaminate(engine, historical=True)
    now = datetime(2026, 7, 16, 12, 0, tzinfo=UTC).replace(tzinfo=None)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO session_timers "
                "(id, session_id, step_id, label, duration_seconds, state, created_at, "
                " started_at, deadline_at, remaining_seconds_at_pause, paused_at, "
                " resolved_at, correlation_id, version) "
                "VALUES ('timer-1', 'session-1', 'step-1', 'Incubate', 600, 'RUNNING', "
                " :now, :now, NULL, NULL, NULL, NULL, 'corr-timer', 1)"
            ),
            {"now": now},
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT label FROM session_timers WHERE id = 'timer-1'")
            ).scalar_one()
            == "Incubate"
        )


def test_an_incompatible_pre_created_table_fails_loudly(tmp_path: Path) -> None:
    """A table that only shares a name must stop the migration, not be reused."""

    url = sqlite_url(tmp_path / "incompatible.db")
    config = alembic_config(url)
    command.upgrade(config, CONTAMINATED_REVISION)
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE session_timers (id VARCHAR(36) NOT NULL)"))

    with pytest.raises(RuntimeError, match="session_timers"):
        command.upgrade(config, "head")


# -------------------------------------------------------------- down and up


def test_0006_downgrades_and_upgrades_again(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "down-0006.db")
    config = alembic_config(url)
    command.upgrade(config, "head")
    engine = create_engine(url)

    command.downgrade(config, "0005_utterance_cross_checks")
    columns = {column["name"] for column in inspect(engine).get_columns("utterances")}
    assert "audio_sha256" not in columns

    command.upgrade(config, "head")

    assert_at_head(engine)
    assert_schema_is_complete(engine)


def test_head_downgrades_to_base_and_upgrades_again(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "round-trip.db")
    config = alembic_config(url)
    command.upgrade(config, "head")
    engine = create_engine(url)

    command.downgrade(config, "base")
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}

    command.upgrade(config, "head")

    assert_at_head(engine)
    assert_schema_is_complete(engine)


# ------------------------------------------------------------------ PostgreSQL


def docker_available() -> bool:
    executable = shutil.which("docker")
    if executable is None:
        return False
    try:
        probe = subprocess.run(
            [executable, "info"],
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


def test_postgres_compose_configuration_parses() -> None:
    """Always runnable. Proves the Postgres topology is at least well-formed.

    This is *not* a migration run. When Docker is unavailable the PostgreSQL
    migration remains unverified, and the readiness record says so rather than
    implying this test covered it.
    """

    compose = REPOSITORY_ROOT / "docker-compose.postgres.yml"
    assert compose.is_file()

    yaml = pytest.importorskip("yaml")
    parsed: dict[str, Any] = yaml.safe_load(compose.read_text(encoding="utf-8"))

    services = parsed["services"]
    assert "database" in services
    assert str(services["database"]["image"]).startswith("postgres:")
    # The API must point at the Postgres service rather than the SQLite default.
    api_url = services["api"]["environment"]["LABJARVIS_DATABASE_URL"]
    assert api_url.startswith("postgresql+psycopg://")
    assert services["api"]["depends_on"]["database"]["condition"] == "service_healthy"


@pytest.mark.skipif(not docker_available(), reason="Docker daemon is not available")
def test_postgres_migrates_from_contaminated_state() -> None:  # pragma: no cover
    """Run the same reconciliation against PostgreSQL when a daemon exists.

    Skipped rather than faked when Docker is absent; a skip is reported as an
    unverified item, never as a pass.
    """

    pytest.importorskip("testcontainers")
    from testcontainers.community.postgres import (  # type: ignore[import-untyped]
        PostgresContainer,
    )

    with PostgresContainer("postgres:16-alpine") as container:
        url = container.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql+psycopg://"
        )
        config = alembic_config(url)
        command.upgrade(config, CONTAMINATED_REVISION)
        engine = create_engine(url)
        seed_pre_migration_data(engine)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE session_timers ("
                    " id VARCHAR(36) NOT NULL PRIMARY KEY,"
                    " session_id VARCHAR(36) NOT NULL REFERENCES sessions (id)"
                    "   ON DELETE CASCADE,"
                    " step_id VARCHAR(80),"
                    " label VARCHAR(120) NOT NULL,"
                    " duration_seconds INTEGER NOT NULL,"
                    " state VARCHAR(20) NOT NULL,"
                    " created_at TIMESTAMP WITH TIME ZONE NOT NULL,"
                    " started_at TIMESTAMP WITH TIME ZONE NOT NULL,"
                    " deadline_at TIMESTAMP WITH TIME ZONE,"
                    " remaining_seconds_at_pause INTEGER,"
                    " paused_at TIMESTAMP WITH TIME ZONE,"
                    " resolved_at TIMESTAMP WITH TIME ZONE,"
                    " correlation_id VARCHAR(80) NOT NULL,"
                    " version INTEGER NOT NULL)"
                )
            )

        command.upgrade(config, "head")

        assert_at_head(engine)
        assert_schema_is_complete(engine)
        assert_seeded_rows_survived(engine)
        assert_requirement_ids_backfilled(engine)


def test_expected_revision_matches_the_migration_head() -> None:
    """The startup guard's constant must track the migration history.

    ``EXPECTED_SCHEMA_REVISION`` is a constant because the installed package
    does not ship the migration scripts. This is what stops it going stale: add
    a revision without updating it and the suite fails here.
    """

    from alembic.script import ScriptDirectory

    from labjarvis.infrastructure.database import EXPECTED_SCHEMA_REVISION

    heads = ScriptDirectory.from_config(alembic_config("sqlite://")).get_heads()

    assert list(heads) == [EXPECTED_SCHEMA_REVISION]
    assert EXPECTED_SCHEMA_REVISION == HEAD_REVISION
