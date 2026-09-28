# ADR 0019: In-app configuration, applied only at startup

- **Status:** Accepted (2026-09-27). Extends ADR 0016.
- **Code:** `saved_configuration.py`, `settings.py` (saved-configuration
  source), `api/routes/configuration.py`, `infrastructure/restart.py`,
  `scripts/supervisor.py`, `scripts/tasks.py dev`,
  `features/settings/SettingsView.tsx`.

## Context

Choosing how the AI stages run (local, hosted, hybrid, per-stage models, CPU or
GPU) meant editing environment variables or remembering different start
commands. That is error-prone for a researcher who is not a developer, and it
is where privacy mistakes would happen.

The obvious alternative, switching models inside the running process, would
have to unload and reload GPU models, drain in-flight inference, re-point
provenance mid-session and keep the hosted-processing opt-in consistent while
adapters change underneath a session. None of that improves a laboratory
record.

## Decision

1. **Configure in the app, apply at startup.** The Settings page saves a small
   schema of choices (per-stage provider, model, CPU/GPU, reasoning effort;
   hands-free; high-integrity transcription; allow hosted processing) to
   `<data dir>/settings.json`. It is read by `get_settings()` on the normal
   startup path as one pydantic-settings source, so the saved choices pass
   through exactly the validators every other configuration does. No model is
   ever reloaded in a running process.
2. **Presets are data.** Local, Hosted and Hybrid are fixed values of the same
   schema that Custom edits; there is one mapping from choices to `Settings`.
3. **Validate the whole proposal, never correct it.** Saving builds the
   next-start `Settings` from the proposal (plus the hands-free model
   preflight). Plain-language checks run first for the common mistakes; any
   failure is reported and nothing is saved.
4. **Active vs saved by projection.** The running settings and the next-start
   settings are projected onto the same schema; "restart required" is their
   inequality. Environment overrides are found the same way (saved value versus
   what the next start would use), so the page never implies an overridden
   choice is active.
5. **Precedence, per variable:** process environment > saved choices > `.env`
   > defaults. Keys and advanced overrides live in `.env`, which every start
   (including a supervised restart) re-reads. The launcher reuses its own
   environment for every restart, so a variable exported there would keep
   winning over `.env`; standard precedence is kept (containers and CI rely on
   it), but the launcher warns at startup and Settings shows each key's source
   and warns about shadowing variables, duplicate OpenAI key names and `.env`
   lines that saved choices make ineffective.
   Credentials and endpoint URLs are not part of the schema: they stay in the
   environment, and the API reports only whether a key is configured.
6. **Restart is one narrow action.** `tasks.py dev` supervises the API: it
   writes a random token to `.labjarvis/supervisor/supervisor.json` and passes
   it to the API. `POST /api/v1/settings/restart` (loopback only, body is a
   single consent flag, extra fields rejected) checks the saved configuration
   would start, requires consent during an active session, writes the token to
   `restart-request`, and stops the API through the Ctrl+C path. The supervisor
   restarts only its fixed API command, only on a matching token. Without a
   supervisor the API reports that a manual restart is needed.
7. **Named providers never share a connection.** OpenAI and DeepSeek are
   separate providers with their own key, endpoint and options
   (`Settings.connection_for`), so both can be configured together and the
   text stage switched between Local, OpenAI and DeepSeek from Settings.
   Per-stage endpoint and key overrides belong to the generic
   OpenAI-compatible provider only.
8. **Fail closed at startup.** A saved configuration that is invalid in the
   current environment stops startup with the reason; LabJarvis never falls
   back to different models or a different locality on its own.

## Consequences

- Configuration is predictable: what is running is exactly what was loaded at
  startup, and provenance, privacy and state-management boundaries are
  unchanged.
- Changing a model costs a restart of a few seconds; persisted protocol,
  session and notebook state is unaffected, but unsubmitted browser input is
  lost, which the confirmation says.
- Not added, deliberately: hot-swapping, per-service restarts, fallback between
  providers, endpoint URLs or keys in the browser, settings authentication,
  versioned configuration, or a general process manager.
