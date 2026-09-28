"""Clean-checkout bootstrap: a file-backed SQLite URL must create its directory.

A fresh clone has no ``.labjarvis`` directory. SQLite refuses to create a
database inside a missing directory, so both the migration entry point and the
runtime session factory have to materialise the parent themselves.
"""

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ArgumentError

from labjarvis.infrastructure.database import (
    create_session_factory,
    ensure_sqlite_directory,
)


def test_creates_missing_parent_directory_for_file_backed_sqlite(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "deeper" / "labjarvis.db"
    assert not target.parent.exists()

    ensure_sqlite_directory(f"sqlite+pysqlite:///{target.as_posix()}")

    assert target.parent.is_dir()


def test_ignores_in_memory_sqlite(tmp_path: Path) -> None:
    del tmp_path
    ensure_sqlite_directory("sqlite+pysqlite://")
    ensure_sqlite_directory("sqlite+pysqlite:///:memory:")


def test_ignores_non_sqlite_urls() -> None:
    ensure_sqlite_directory("postgresql+psycopg://user@localhost:5432/labjarvis")


def test_relative_url_without_directory_component_is_left_alone(tmp_path: Path) -> None:
    del tmp_path
    ensure_sqlite_directory("sqlite+pysqlite:///labjarvis.db")


def test_session_factory_opens_a_database_in_a_missing_directory(tmp_path: Path) -> None:
    target = tmp_path / "absent" / "labjarvis.db"

    factory = create_session_factory(f"sqlite+pysqlite:///{target.as_posix()}", create_schema=True)

    with factory() as session:
        assert session.execute(text("SELECT 1")).scalar_one() == 1
    assert target.is_file()


def test_blank_url_is_ignored_so_alembic_defaults_still_apply() -> None:
    ensure_sqlite_directory("")
    ensure_sqlite_directory("   ")


def test_malformed_url_is_reported_as_a_configuration_error() -> None:
    with pytest.raises(ArgumentError):
        ensure_sqlite_directory("not-a-url")
