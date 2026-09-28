"""Migration 0004 must upgrade real pre-existing rows, not just an empty schema.

An approved protocol version written before requirement identity existed has to
keep working, so the migration rewrites its stored steps in place.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

REPOSITORY_ROOT = Path(__file__).parents[4]

LEGACY_STEPS = [
    {
        "duration_seconds": None,
        "id": "step-1",
        "instruction": "Add 10 mL ethanol and 250 uL buffer.",
        "number": 1,
        "parallel_allowed": False,
        "requirements": [
            {"aliases": [], "name": "ethanol", "quantity": "10", "unit": "mL"},
            {"aliases": [], "name": "buffer", "quantity": "250", "unit": "uL"},
        ],
        "safety_notes": [],
        "source": {"excerpt": "Add reagents.", "page": 1, "section": None},
        "title": "Add reagents",
    }
]


def _config(database_url: str) -> Config:
    config = Config(str(REPOSITORY_ROOT / "apps" / "api" / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _seed_legacy_version(database_url: str) -> None:
    engine = create_engine(database_url)
    now = datetime.now(tz=UTC).isoformat()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO protocols (id, title, created_at) "
                "VALUES ('protocol-1', 'Legacy protocol', :now)"
            ),
            {"now": now},
        )
        connection.execute(
            text(
                "INSERT INTO protocol_versions "
                "(id, protocol_id, version_number, revision, status, steps_json, "
                " source_sha256, source_text, created_at, approved_at) "
                "VALUES ('version-1', 'protocol-1', 1, 1, 'APPROVED', :steps, "
                " :sha, 'Add reagents.', :now, :now)"
            ),
            {
                "steps": json.dumps(LEGACY_STEPS, separators=(",", ":"), sort_keys=True),
                "sha": "0" * 64,
                "now": now,
            },
        )


def _stored_steps(database_url: str) -> list[dict[str, object]]:
    engine = create_engine(database_url)
    with engine.connect() as connection:
        raw = connection.execute(
            text("SELECT steps_json FROM protocol_versions WHERE id = 'version-1'")
        ).scalar_one()
    parsed: list[dict[str, object]] = json.loads(raw)
    return parsed


def test_legacy_requirements_are_backfilled_with_positional_identifiers(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'backfill.db').as_posix()}"
    config = _config(database_url)
    command.upgrade(config, "0003_verification_review")
    _seed_legacy_version(database_url)

    command.upgrade(config, "0004_durable_timers_and_requirement_ids")

    requirements = _stored_steps(database_url)[0]["requirements"]
    assert isinstance(requirements, list)
    assert [item["requirement_id"] for item in requirements] == [
        "step-1-req-1",
        "step-1-req-2",
    ]
    # The rest of the requirement is untouched.
    assert requirements[0]["name"] == "ethanol"
    assert requirements[1]["quantity"] == "250"


def test_downgrade_restores_the_previous_shape_without_losing_reagents(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'downgrade.db').as_posix()}"
    config = _config(database_url)
    command.upgrade(config, "0003_verification_review")
    _seed_legacy_version(database_url)
    command.upgrade(config, "0004_durable_timers_and_requirement_ids")

    command.downgrade(config, "0003_verification_review")

    requirements = _stored_steps(database_url)[0]["requirements"]
    assert isinstance(requirements, list)
    assert all("requirement_id" not in item for item in requirements)
    assert [item["name"] for item in requirements] == ["ethanol", "buffer"]
