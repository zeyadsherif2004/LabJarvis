import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from labjarvis.infrastructure.database import ensure_sqlite_directory, metadata

config = context.config
environment_database_url = os.getenv("LABJARVIS_DATABASE_URL")
if environment_database_url:
    config.set_main_option("sqlalchemy.url", environment_database_url.replace("%", "%%"))
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata
# A clean checkout has no data directory yet; SQLite will not create one.
ensure_sqlite_directory(config.get_main_option("sqlalchemy.url") or "")


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
