"""Starting the application must never create or mutate schema. Alembic owns it.

The reported defect, reproduced exactly:

1. an empty SQLite database;
2. migrated to ``0003_verification_review``;
3. ``create_app()`` run against it;
4. migrated to head;
5. ``0004`` dies on ``table session_timers already exists``.

Step 3 is the fault. ``create_session_factory`` defaulted to
``create_schema=True`` and ``create_app`` took that default, so every start
wrote current metadata into a database several revisions behind.

Migrations 0004-0007 now reconcile that state, so a database already damaged
this way can be recovered - but recovery is not a licence to keep damaging new
ones. These tests pin the bootstrap behaviour itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from labjarvis.api.app import create_app
from labjarvis.infrastructure.database import (
    EXPECTED_SCHEMA_REVISION,
    SchemaOutOfDate,
    create_session_factory,
    verify_schema_is_current,
)
from labjarvis.settings import Settings

REPOSITORY_ROOT = Path(__file__).parents[4]


@pytest.fixture(autouse=True)
def _isolate_from_developer_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LABJARVIS_DATABASE_URL", raising=False)


def alembic_config(database_url: str) -> Config:
    config = Config(str(REPOSITORY_ROOT / "apps" / "api" / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def sqlite_url(path: Path) -> str:
    return f"sqlite+pysqlite:///{path.as_posix()}"


def test_session_factory_does_not_create_schema_by_default(tmp_path: Path) -> None:
    """The default is the production default, and production migrates first."""

    target = tmp_path / "untouched.db"

    create_session_factory(sqlite_url(target))

    tables = set(inspect(create_engine(sqlite_url(target))).get_table_names())
    assert tables == set()


def test_schema_creation_is_available_but_must_be_asked_for(tmp_path: Path) -> None:
    """Isolated tests opt in explicitly; nothing reads it from the environment."""

    target = tmp_path / "explicit.db"

    create_session_factory(sqlite_url(target), create_schema=True)

    tables = set(inspect(create_engine(sqlite_url(target))).get_table_names())
    assert "session_timers" in tables


def test_starting_the_app_against_an_older_revision_creates_nothing(
    tmp_path: Path,
) -> None:
    """The regression. Before the fix this wrote current tables at revision 0003."""

    target = tmp_path / "behind.db"
    url = sqlite_url(target)
    config = alembic_config(url)
    command.upgrade(config, "0003_verification_review")
    settings = Settings(app_env="test", data_dir=tmp_path, database_url=url)

    with pytest.raises(SchemaOutOfDate):
        create_app(settings)

    # Nothing was created behind the migration's back.
    tables = set(inspect(create_engine(url)).get_table_names())
    assert "session_timers" not in tables
    assert "utterances" not in tables


def test_a_database_left_behind_can_still_be_migrated_afterwards(
    tmp_path: Path,
) -> None:
    """Refusing to start must leave the database migratable, not wedged."""

    target = tmp_path / "recoverable.db"
    url = sqlite_url(target)
    config = alembic_config(url)
    command.upgrade(config, "0003_verification_review")
    settings = Settings(app_env="test", data_dir=tmp_path, database_url=url)
    with pytest.raises(SchemaOutOfDate):
        create_app(settings)

    command.upgrade(config, "head")

    with create_engine(url).connect() as connection:
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    assert revision == EXPECTED_SCHEMA_REVISION


def test_app_starts_normally_against_a_migrated_database(tmp_path: Path) -> None:
    target = tmp_path / "current.db"
    url = sqlite_url(target)
    command.upgrade(alembic_config(url), "head")
    settings = Settings(app_env="test", data_dir=tmp_path, database_url=url)

    app = create_app(settings)

    assert app.title


def test_an_unmigrated_database_is_reported_as_such(tmp_path: Path) -> None:
    """An empty database is a configuration mistake, not something to fix silently."""

    settings = Settings(
        app_env="test",
        data_dir=tmp_path,
        database_url=sqlite_url(tmp_path / "empty.db"),
    )

    with pytest.raises(SchemaOutOfDate, match="has not been migrated"):
        create_app(settings)


def test_the_error_names_the_command_that_fixes_it(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "message.db")
    command.upgrade(alembic_config(url), "0003_verification_review")

    with pytest.raises(SchemaOutOfDate) as caught:
        verify_schema_is_current(url)

    message = str(caught.value)
    assert "0003_verification_review" in message
    assert EXPECTED_SCHEMA_REVISION in message
    assert "alembic" in message
    # Never suggest the one repair that would hide the problem.
    assert "stamp" not in message


def test_verification_passes_at_head(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "head.db")
    command.upgrade(alembic_config(url), "head")

    verify_schema_is_current(url)


def test_verification_is_skipped_for_a_blank_url() -> None:
    """A blank URL is a configuration error the settings layer reports, not here."""

    verify_schema_is_current("")
