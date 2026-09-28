# LabJarvis API

The FastAPI backend for LabJarvis (package `labjarvis`, Python 3.12).

| Path | Contents |
|---|---|
| `src/labjarvis/api` | app factory (`create_app`), routes (incl. `configuration.py` for Settings/restart and `speech_output.py` for the Kokoro voice), middleware, origin guard, error mapping |
| `src/labjarvis/application` | use cases: `service.py` facade plus confirmation, reagent verification, session orchestration, protocol lifecycle, notebook export, backup, data management, tool registry |
| `src/labjarvis/domain` | framework-free rules: state machine, commands, confirmations, voice-only confirmation, entity grounding, reagent comparison and consensus, live-scan rule, wake phrase, grounding |
| `src/labjarvis/ports` | model, repository, media and realtime interfaces |
| `src/labjarvis/infrastructure` | adapters: SQLAlchemy (`db/schema.py`), fake/local/hosted models, rule grammar, cascade, command contract, interpreter spec, model factory, warm-up, study mode, Kokoro voice output (`speech_output.py`), restart protocol (`restart.py`) |
| `src/labjarvis/evaluation` | evaluation runner, metrics, safety gate, paid-call spend ledger |
| `src/labjarvis/settings.py` | every `LABJARVIS_*` setting (see `/.env.example`) and the source precedence |
| `src/labjarvis/saved_configuration.py` | Settings-page choices: presets, validation, `settings.json`, active vs saved, credential status |
| `migrations/` | Alembic revisions `0001`-`0009` (`alembic.ini`) |
| `tests/` | unit, integration, contract, property and opt-in model-smoke tests |

Dependencies are pinned in `requirements*.lock` (`requirements-dev.lock` for
development, `-models` for local models and the Kokoro voice, `-postgres` for
the container). Setup,
commands and safety guidance are in the repository [README](../../README.md) and
[docs](../../docs/README.md); the HTTP contract is in
[docs/development/api.md](../../docs/development/api.md).
