"""Introspection helpers that let a migration reconcile a contaminated database.

``create_app()`` used to call ``metadata.create_all()`` on startup, which
created *current* tables into a database stamped at an older revision. The
migrations that own those tables then failed with ``table already exists``, and
the database could not be moved forward at all.

The migrations therefore have to ask what is actually there before acting. Three
rules keep that from becoming "skip anything that looks done", which would be a
different and quieter kind of corruption:

1. A pre-created table is **reused only when its columns match** what the
   migration would have created. Anything else raises, because silently adopting
   a table that merely shares a name is how a schema drifts out of step with the
   code that reads it.
2. Reuse suppresses **only the ``CREATE``**. Every column, index, and data
   backfill the migration also performs still runs, because those are what the
   pre-creation did *not* do.
3. Everything here goes through SQLAlchemy's ``Inspector``, so it behaves the
   same on SQLite and PostgreSQL. No migration branches on dialect.

These helpers live in the installed package rather than beside the migration
scripts so they can be imported normally and unit-tested.
"""

from __future__ import annotations

from sqlalchemy import Connection, inspect


class IncompatibleExistingTable(RuntimeError):
    """An existing table shares a migration's name but not its shape.

    Deliberately fatal. The alternative - carrying on against a table whose
    columns are unknown - produces a database that appears migrated and is not.
    """


def table_exists(connection: Connection, table: str) -> bool:
    return inspect(connection).has_table(table)


def column_names(connection: Connection, table: str) -> set[str]:
    return {column["name"] for column in inspect(connection).get_columns(table)}


def column_exists(connection: Connection, table: str, column: str) -> bool:
    return table_exists(connection, table) and column in column_names(connection, table)


def index_exists(connection: Connection, table: str, index: str) -> bool:
    if not table_exists(connection, table):
        return False
    return index in {entry["name"] for entry in inspect(connection).get_indexes(table)}


def adopt_existing_table(
    connection: Connection,
    table: str,
    *,
    required_columns: set[str],
    revision: str,
) -> bool:
    """Decide whether a table that already exists may be reused.

    Returns ``True`` when the table is absent and the caller should create it.
    Returns ``False`` when an existing table is compatible and may be adopted.
    Raises {@link IncompatibleExistingTable} when it exists but is not.

    "Compatible" means the table has at least every column the migration would
    have created. Extra columns are tolerated on purpose: runtime schema
    creation could have run against a *newer* metadata than this revision knew
    about, so ``utterances`` may already carry the columns a later migration
    adds. Those later migrations check for their own columns individually.
    """

    if not table_exists(connection, table):
        return True

    present = column_names(connection, table)
    missing = required_columns - present
    if missing:
        raise IncompatibleExistingTable(
            f"Migration {revision} found an existing '{table}' table that is not "
            f"the one it creates: missing column(s) {sorted(missing)}. "
            f"Present columns: {sorted(present)}. "
            "Refusing to continue: this database needs manual inspection, not an "
            "automatic repair. Do not resolve this with 'alembic stamp'."
        )
    return False
