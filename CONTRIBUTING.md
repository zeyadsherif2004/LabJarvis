# Contributing

LabJarvis is a safety-adjacent academic prototype. Contributions should preserve its narrow scope, local-first defaults, deterministic control plane, auditability, and evidence honesty.

## Setup

Use Python 3.12, Node.js 22.13+, and pnpm 11.9.0:

```powershell
python scripts/tasks.py setup
python scripts/tasks.py migrate
```

See [developer setup](docs/development/setup.md) for platform notes and troubleshooting.

## Change workflow

1. Create a focused branch and describe the user-visible or engineering reason for the change.
2. Trace the flow across API schema, application use case, domain invariant, adapter, persistence, UI, and export before editing.
3. Add or update the nearest unit test first for behavior changes; add integration/E2E coverage when boundaries or principal workflows change.
4. Keep model code behind a port. Never let an LLM/OCR/STT adapter mutate state or persist records directly.
5. Update settings, migrations, types, API contracts, documentation, evaluation fixtures, and threat controls when affected.
6. Run the checks below and report skipped/unavailable verification honestly.

```powershell
python scripts/tasks.py ci          # lint, typecheck, tests (backend, research, frontend, E2E), build, secret scan
python scripts/tasks.py evaluate
python scripts/tasks.py security    # pip-audit and pnpm audit (network)
```

Tests are hermetic: ambient `LABJARVIS_*` variables and `.env` are ignored.
Never run paid hosted commands (`smoke-hosted`, `test-hosted-smoke`,
`test-realtime-smoke`) as part of a routine check.

## Coding standards

- Python: typed Python 3.12, Ruff, strict mypy, immutable domain dataclasses, Pydantic at boundaries, SQLAlchemy only in infrastructure.
- TypeScript: strict types, semantic HTML, accessible names, no color-only status, keyboard operation, reduced-motion support.
- APIs: versioned routes, strict schemas, stable Problem Details codes, correlation IDs, idempotency/version checks for mutations.
- Data: append corrections and decisions; do not rewrite audit history. Do not log bodies or sensitive model input.
- Tests: deterministic and independent; no network/model downloads by default. Mark synthetic fixtures visibly.
- Documentation: distinguish verified facts, inferences, provisional choices, pending evidence, and student-only work.

## Dependencies and model changes

Explain why a dependency is needed, regenerate the relevant lock files with `python scripts/tasks.py lock`, and run dependency scans. A new model adapter needs the port contract, configuration, health behavior, fake-safe tests, opt-in smoke test, model card/licence/source record, and candidate evaluation entry described in [the adapter guide](docs/development/model-adapters.md).

## Pull request checklist

- [ ] Scope and safety impact explained.
- [ ] Types, tests, migrations, docs, and configs updated together.
- [ ] No secret, model weight, raw participant/lab data, database, or generated benchmark output committed.
- [ ] Fake mode remains offline and deterministic.
- [ ] Lint, typing, tests, build, E2E, evaluation-fixture run, and security checks reported with actual outcomes.
- [ ] New limitations or human verification steps are explicit.

