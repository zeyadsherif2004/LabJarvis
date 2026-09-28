# Local deployment and operations

## Supported profiles

| Profile | UI/API | Database | Models | Intended use |
|---|---|---|---|---|
| development fake | Vite + Uvicorn | SQLite | deterministic fakes | implementation/demo/CI-equivalent testing |
| local real | Vite + Uvicorn | SQLite | local faster-whisper/Ollama/EasyOCR (CPU or GPU), Kokoro voice | controlled model integration/evaluation |
| hybrid / hosted | Vite + Uvicorn | SQLite | any stage on OpenAI, DeepSeek (text) or an OpenAI-compatible provider (explicit opt-in) | operators without suitable local hardware; see [privacy](privacy-retention.md) |
| container fake | nginx + API containers | SQLite volume | fakes | reproducible loopback demo |
| container PostgreSQL | above + database container | PostgreSQL volume | fakes by default | production-like persistence exercise |

Network-exposed/multi-user deployment is unsupported.

## Process lifecycle

```powershell
python scripts/tasks.py demo         # deterministic fakes; ignores saved Settings
python scripts/tasks.py dev          # Settings choices + .env + defaults
python scripts/tasks.py hands-free   # dev with the hands-free profile and a kiosk window
```

All migrate first, start API/web child processes, and stop the process tree on
Ctrl+C. They also supervise the API: **Settings -> Restart LabJarvis** asks the
launcher to stop the API gracefully and start the same fixed command again, so
the saved configuration takes effect while the web server keeps running (the
page reconnects by itself). The launcher warns at startup about any
`LABJARVIS_*` variable inherited from the shell, because those override both
`.env` and Settings for as long as it runs. Check `/health/live` for process liveness and `/health/ready` for model/profile/retention readiness. A local-mode API may be live but not ready when a model/cache/service is unavailable.

## Containers

```powershell
docker compose config --quiet
docker compose up --build
```

UI: <http://127.0.0.1:8080>; API: <http://127.0.0.1:8000>. Data persists in `labjarvis-data`.

PostgreSQL:

```powershell
$env:POSTGRES_PASSWORD="use-a-strong-local-secret"
docker compose -f docker-compose.yml -f docker-compose.postgres.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up --build
```

Compose health-gates the web/database dependencies. The published ports remain loopback-only. Both Compose files run the API in **fake** mode; model containers/caches are not bundled, so local-model mode is run directly on the workstation (`python scripts/tasks.py dev`). The API image installs `requirements-postgres.lock` (no model dependencies, so no Kokoro voice: the browser voice speaks). Configure containers through `LABJARVIS_*` environment variables: the Settings page can show the configuration but cannot change it or restart there, because requests arrive through nginx rather than from loopback and no restart supervisor runs.

Both container profiles were built and exercised on 2026-08-19 with synthetic
data. The SQLite profile passed health, upload persistence across an API
restart, backup, and staged-restore validation. The PostgreSQL profile used
PostgreSQL 18.2-alpine and passed health, fresh and contaminated migrations
through `0009_session_labels_archive`, and upload persistence across database
and API restarts. A native `pg_dump -Fc` / `pg_restore` drill restored one
protocol version into a disposable `labjarvis_restore` database at that
revision. The application backup endpoint returned the expected 409
`BACKUP_PROFILE_UNSUPPORTED` on PostgreSQL. A separate PostgreSQL 16
contamination test preserved existing rows and backfilled requirement IDs.
These are dated runtime checks; the containers were not rebuilt against
revision 0010. The [fresh-copy readiness script](../../scripts/readiness.py)
checks the current source without starting either container profile. All
deployment evidence here is for a loopback, single-operator setup.

## Logs and diagnostics

JSON logs are the default and include timestamp, event, method/path, status, duration, outcome/error code, and correlation ID. They intentionally omit bodies, transcripts, protocol text, OCR labels, and notebook payloads. Use the correlation ID to join an operator-visible failure to audit records without copying sensitive content into logs.

Set `LABJARVIS_LOG_FORMAT=console` only for local readability. Do not enable arbitrary HTTP body logging. Protect terminal history and test traces if real data is used.

## Backup and restore

Before a real session, follow institutional data policy. File-backed SQLite
profiles can create an online, transactionally consistent backup with
`POST /api/v1/data/backups`. Packages are written beneath
`LABJARVIS_BACKUP_DIR` (or `<data-dir>/backups`) and contain the SQLite database,
retained media when present, and a manifest of sizes and SHA-256 hashes. They do
not contain environment configuration or credentials.

`POST /api/v1/data/backups/{backup-name}/validate` rechecks the complete file
set, hashes, schema revision, and SQLite integrity before copying the database to
`<data-dir>/restore-staging`. Validation never replaces the active database.
Activation is deliberately offline: stop LabJarvis, preserve the current data,
then explicitly point `LABJARVIS_DATABASE_URL` at the validated staging database
or replace it under the approved recovery procedure. Retained media must be
restored as the same protected set. A validation response is not evidence that
this offline activation or a functional recovery test occurred.

### Bounded SQLite restore drill

Use synthetic data for a drill. Never overwrite the only copy of an active
database.

1. Export the synthetic session, create a backup from **Data**, and record its
   `backup_name` and manifest hash.
2. Add a clearly identifiable synthetic record after the backup so the active
   and backup states differ.
3. Validate the backup. Confirm the response names
   `<data-dir>/restore-staging/<backup-name>.sqlite3` and reports the expected
   schema revision and file hashes.
4. Stop every LabJarvis API process. Copy the staged database and matching
   retained-media set into a new protected drill directory; do not modify the
   source package or current data directory.
5. Point `LABJARVIS_DATABASE_URL` at that copied SQLite file, run
   `python scripts/tasks.py migrate`, and start the API on loopback.
6. Require `/health/ready` to succeed in fake mode, open the restored synthetic
   session, compare its JSON export with the pre-backup export, and confirm the
   post-backup marker is absent.
7. Stop the drill process, restore the original configuration, and record the
   commit, backup name, commands, timestamps, and comparison result. Delete
   drill copies only under the applicable retention plan.

Keep the data, backup, staging, and deletion-receipt directories accessible only
to the operator account. The application does not configure full-disk
encryption, backup encryption, cloud-sync exclusions, or operating-system ACLs
for you; verify those controls on the deployment host.

For PostgreSQL, the HTTP backup operation is refused. Use version-compatible
`pg_dump`/`pg_restore` with credentials supplied outside Git, restore first into
a disposable database, and run schema/application checks before switching.
Volume existence is not a backup. Record encryption, access, retention, and
restore tests.

Exports are evidence extracts, not a full database backup: JSON includes notebook/inference/verification/audit context; CSV is intentionally tabular.

## Upgrade procedure

1. Stop captures and resolve/reject pending decisions.
2. Back up data and record the current commit/config/model digests.
3. Install the new exact locks/build images.
4. Run migration upgrade on a disposable copy first.
5. Run fake-mode tests/build/evaluation and relevant real smoke tests.
6. Upgrade the real database, start loopback services, inspect readiness, and exercise a synthetic session.
7. Keep rollback instructions/data compatible with the migration guide.

## Capacity and recovery limits

Inference is intentionally bounded and serialized by default. There is no queue durability, HA, replication, automatic purge, rate limiter, or external monitoring. The UI is for one operator. If availability is essential, maintain the physical protocol and independent notebook and follow lab contingency procedures.
