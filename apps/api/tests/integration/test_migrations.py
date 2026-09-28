import importlib.util
import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from labjarvis.infrastructure.database import metadata

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


def test_first_long_revision_widens_the_postgres_version_column(monkeypatch) -> None:
    repository_root = Path(__file__).parents[4]
    migration_path = (
        repository_root
        / "apps"
        / "api"
        / "migrations"
        / "versions"
        / "0004_durable_timers_and_requirement_ids.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0004", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class Dialect:
        name = "postgresql"

    class Connection:
        dialect = Dialect()

    monkeypatch.setattr(
        migration.op,
        "alter_column",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert len(migration.revision) > 32
    migration._widen_alembic_version_column(Connection())

    assert calls[0][0] == ("alembic_version", "version_num")
    assert calls[0][1]["type_"].length == 128


def test_initial_migration_creates_complete_schema(tmp_path) -> None:
    repository_root = Path(__file__).parents[4]
    config = Config(str(repository_root / "apps" / "api" / "alembic.ini"))
    database_url = f"sqlite+pysqlite:///{tmp_path / 'migration.db'}"
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, "head")

    inspector = inspect(create_engine(database_url))
    tables = set(inspector.get_table_names())
    assert tables >= EXPECTED_TABLES
    for table in metadata.sorted_tables:
        migrated_columns = {column["name"] for column in inspector.get_columns(table.name)}
        assert migrated_columns == set(table.c.keys())


def test_initial_migration_can_downgrade_to_empty_schema(tmp_path) -> None:
    repository_root = Path(__file__).parents[4]
    config = Config(str(repository_root / "apps" / "api" / "alembic.ini"))
    database_url = f"sqlite+pysqlite:///{tmp_path / 'downgrade.db'}"
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    command.downgrade(config, "base")

    remaining = set(inspect(create_engine(database_url)).get_table_names())
    assert remaining <= {"alembic_version"}


def test_state_integrity_migration_reconciles_duplicates_before_unique_indexes(
    tmp_path,
) -> None:
    repository_root = Path(__file__).parents[4]
    config = Config(str(repository_root / "apps" / "api" / "alembic.ini"))
    database_url = f"sqlite+pysqlite:///{tmp_path / 'state-integrity.db'}"
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0006_spoken_confirmations")
    engine = create_engine(database_url)

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO protocols (id, title, created_at) "
                "VALUES ('protocol-1', 'Synthetic protocol', '2026-08-19 08:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO protocol_versions "
                "(id, protocol_id, version_number, revision, status, steps_json, "
                "source_sha256, source_text, created_at, approved_at) VALUES "
                "('version-1', 'protocol-1', 1, 1, 'APPROVED', :steps, :sha, '', "
                "'2026-08-19 08:00:00', '2026-08-19 08:00:00')"
            ),
            {
                "steps": '[{"id":"step-1","number":1,"title":"Mix",'
                '"instruction":"Mix","requirements":[],"duration_seconds":null,'
                '"safety_notes":[],"source":{"page":1,"section":null,'
                '"excerpt":"Mix"},"parallel_allowed":false}]',
                "sha": "a" * 64,
            },
        )
        connection.execute(
            text(
                "INSERT INTO sessions "
                "(id, protocol_version_id, protocol_approved, state, steps_json, "
                "active_step_id, version, created_at, started_at, ended_at) VALUES "
                "('session-1', 'version-1', 1, 'ACTIVE', :steps, 'step-1', 3, "
                "'2026-08-19 08:00:00', '2026-08-19 08:00:00', NULL)"
            ),
            {"steps": '[{"step_id":"step-1","state":"IN_PROGRESS"}]'},
        )
        for action_id, created_at, expected_version in (
            ("action-old", "2026-08-19 08:01:00", 2),
            ("action-new", "2026-08-19 08:02:00", 3),
        ):
            connection.execute(
                text(
                    "INSERT INTO pending_actions "
                    "(id, session_id, intent, payload_json, source, status, "
                    "expected_session_version, created_at, correlation_id, "
                    "model_inference_id, resolved_at) VALUES "
                    "(:id, 'session-1', 'complete_step', '{}', 'model_proposal', "
                    "'PENDING', :expected_version, :created_at, :correlation_id, '', NULL)"
                ),
                {
                    "id": action_id,
                    "expected_version": expected_version,
                    "created_at": created_at,
                    "correlation_id": f"correlation-{action_id}",
                },
            )
        for verification_id, created_at in (
            ("verification-old", "2026-08-19 08:01:00"),
            ("verification-new", "2026-08-19 08:02:00"),
        ):
            connection.execute(
                text(
                    "INSERT INTO reagent_verifications "
                    "(id, session_id, step_id, requirement_id, expected_label, "
                    "observed_label, outcome, score, expected_normalised, "
                    "observed_normalised, match_threshold, uncertain_threshold, "
                    "model_confidence, decision, model_inference_id, created_at, resolved_at) "
                    "VALUES (:id, 'session-1', 'step-1', 'requirement-1', 'NaCl', "
                    "'NaCl', 'MATCH', 1.0, 'nacl', 'nacl', 0.8, 0.6, 1.0, "
                    "'PENDING', '', :created_at, NULL)"
                ),
                {"id": verification_id, "created_at": created_at},
            )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        action_rows = connection.execute(
            text("SELECT id, status FROM pending_actions ORDER BY created_at")
        ).all()
        verification_rows = connection.execute(
            text("SELECT id, decision FROM reagent_verifications ORDER BY created_at")
        ).all()
    assert action_rows == [("action-old", "EXPIRED"), ("action-new", "PENDING")]
    assert verification_rows == [
        ("verification-old", "STALE"),
        ("verification-new", "PENDING"),
    ]

    pending_indexes = {index["name"] for index in inspect(engine).get_indexes("pending_actions")}
    verification_indexes = {
        index["name"] for index in inspect(engine).get_indexes("reagent_verifications")
    }
    assert "uq_pending_actions_one_pending_per_session" in pending_indexes
    assert "uq_reagent_verifications_one_pending_per_requirement" in verification_indexes


def test_step_state_migration_maps_historical_waiting_states_and_preserves_pending_rows(
    tmp_path,
) -> None:
    repository_root = Path(__file__).parents[4]
    config = Config(str(repository_root / "apps" / "api" / "alembic.ini"))
    database_url = f"sqlite+pysqlite:///{tmp_path / 'step-state.db'}"
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0007_state_and_reagent_integrity")
    engine = create_engine(database_url)
    protocol_steps = [
        {
            "id": f"step-{index}",
            "number": index,
            "title": "Historical step",
            "instruction": "Historical instruction",
            "requirements": [],
            "duration_seconds": None,
            "safety_notes": [],
            "source": {"page": 1, "section": None, "excerpt": "Historical"},
            "parallel_allowed": False,
        }
        for index in range(1, 4)
    ]
    session_steps = [
        {
            "step_id": f"step-{index}",
            "state": state,
            "started_at": "2026-08-19T08:00:00+00:00",
            "completed_at": None,
            "skipped_reason": None,
        }
        for index, state in enumerate(
            (
                "AWAITING_INPUT",
                "AWAITING_REAGENT_VERIFICATION",
                "AWAITING_CONFIRMATION",
            ),
            start=1,
        )
    ]
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO protocols (id, title, created_at) "
                "VALUES ('protocol-1', 'Historical protocol', '2026-08-19 08:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO protocol_versions "
                "(id, protocol_id, version_number, revision, status, steps_json, "
                "source_sha256, source_text, created_at, approved_at) VALUES "
                "('version-1', 'protocol-1', 1, 1, 'APPROVED', :steps, :sha, '', "
                "'2026-08-19 08:00:00', '2026-08-19 08:00:00')"
            ),
            {"steps": json.dumps(protocol_steps), "sha": "a" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO sessions "
                "(id, protocol_version_id, protocol_approved, state, steps_json, "
                "active_step_id, version, created_at, started_at, ended_at) VALUES "
                "('session-1', 'version-1', 1, 'ACTIVE', :steps, 'step-3', 1, "
                "'2026-08-19 08:00:00', '2026-08-19 08:00:00', NULL)"
            ),
            {"steps": json.dumps(session_steps)},
        )
        connection.execute(
            text(
                "INSERT INTO pending_actions "
                "(id, session_id, intent, payload_json, source, status, "
                "expected_session_version, created_at, correlation_id, "
                "model_inference_id, resolved_at) VALUES "
                "('action-1', 'session-1', 'complete_step', '{}', 'model_proposal', "
                "'PENDING', 1, '2026-08-19 08:01:00', 'correlation-action', '', NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO reagent_verifications "
                "(id, session_id, step_id, requirement_id, expected_label, "
                "observed_label, outcome, score, expected_normalised, "
                "observed_normalised, match_threshold, uncertain_threshold, "
                "model_confidence, decision, model_inference_id, created_at, resolved_at) "
                "VALUES ('verification-1', 'session-1', 'step-3', 'requirement-1', "
                "'NaCl', 'NaCl', 'MATCH', 1.0, 'nacl', 'nacl', 0.8, 0.6, 1.0, "
                "'PENDING', '', '2026-08-19 08:01:00', NULL)"
            )
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        persisted = connection.execute(
            text("SELECT steps_json FROM sessions WHERE id = 'session-1'")
        ).scalar_one()
        action_status = connection.execute(
            text("SELECT status FROM pending_actions WHERE id = 'action-1'")
        ).scalar_one()
        verification_decision = connection.execute(
            text("SELECT decision FROM reagent_verifications WHERE id = 'verification-1'")
        ).scalar_one()
    assert [step["state"] for step in json.loads(persisted)] == [
        "IN_PROGRESS",
        "IN_PROGRESS",
        "IN_PROGRESS",
    ]
    assert action_status == "PENDING"
    assert verification_decision == "PENDING"


def test_lifecycle_metadata_migration_preserves_existing_aggregates(tmp_path) -> None:
    repository_root = Path(__file__).parents[4]
    config = Config(str(repository_root / "apps" / "api" / "alembic.ini"))
    database_url = f"sqlite+pysqlite:///{tmp_path / 'lifecycle-metadata.db'}"
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0008_simplify_step_states")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO protocols (id, title, created_at) "
                "VALUES ('protocol-1', 'Existing protocol', '2026-08-19 08:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO protocol_versions "
                "(id, protocol_id, version_number, revision, status, steps_json, "
                "source_sha256, source_text, created_at, approved_at) VALUES "
                "('version-1', 'protocol-1', 1, 1, 'APPROVED', '[]', :sha, '', "
                "'2026-08-19 08:00:00', '2026-08-19 08:00:00')"
            ),
            {"sha": "a" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO sessions "
                "(id, protocol_version_id, protocol_approved, state, steps_json, "
                "active_step_id, version, created_at, started_at, ended_at) VALUES "
                "('session-1', 'version-1', 1, 'READY', '[]', NULL, 0, "
                "'2026-08-19 08:00:00', NULL, NULL)"
            )
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        archived_at = connection.execute(
            text("SELECT archived_at FROM protocols WHERE id = 'protocol-1'")
        ).scalar_one()
        label = connection.execute(
            text("SELECT label FROM sessions WHERE id = 'session-1'")
        ).scalar_one()
    assert archived_at is None
    assert label is None
