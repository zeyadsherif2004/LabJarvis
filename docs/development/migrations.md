# Database migration guide

Alembic is configured by `apps/api/alembic.ini`; `migrations/env.py` reads the validated `LABJARVIS_DATABASE_URL`. The schema supports SQLite and PostgreSQL through SQLAlchemy.

## Apply migrations

```powershell
python scripts/tasks.py migrate
```

Direct form:

```powershell
.venv/Scripts/python -m alembic -c apps/api/alembic.ini upgrade head
```

The app does not create tables at startup and refuses to serve a database that is not at the current revision (`0009_session_labels_archive`). `tasks.py dev`, `tasks.py demo`, the E2E runner and the API container migrate first; `tasks.py setup` does not.

## Create a migration

1. Change SQLAlchemy table metadata and related domain/repository mappings together.
2. Generate a candidate:

   ```powershell
   .venv/Scripts/python -m alembic -c apps/api/alembic.ini revision --autogenerate -m "describe change"
   ```

3. Read and edit the generated upgrade/downgrade. Check constraints, data backfill, nullability, defaults, index/foreign-key names, and both database dialects.
4. Update repository mapping, API schemas/exports, fixtures, and tests.
5. Run a clean round trip:

   ```powershell
   $env:LABJARVIS_DATABASE_URL="sqlite+pysqlite:///migration-check.db"
   .venv/Scripts/python -m alembic -c apps/api/alembic.ini upgrade head
   .venv/Scripts/python -m alembic -c apps/api/alembic.ini downgrade base
   .venv/Scripts/python -m alembic -c apps/api/alembic.ini upgrade head
   .venv/Scripts/python -m pytest apps/api/tests/integration/test_migrations.py
   ```

6. Remove the temporary development database after confirming its absolute path.

## Rules

- Never edit a migration already used in shared/published history; add a new revision.
- Never drop/overwrite notebook/audit evidence without an explicit migration and documented consequence.
- Prefer additive/backfilled changes. SQLite batch limitations must be considered.
- Back up a real database before upgrade. Test restoration and downgrade where the migration claims reversibility.
- PostgreSQL verification requires the Compose profile and a running Docker daemon; static config alone is not a migration execution result.

