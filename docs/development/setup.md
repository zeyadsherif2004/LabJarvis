# Developer setup

## Supported baseline

- Python 3.12
- Node.js 22.13 or newer (22.18 is used by the container and final local verification)
- pnpm 11.9.0 (declared in the root package)
- Git
- Chromium for Playwright (`pnpm --dir apps/web exec playwright install chromium`)
- Optional: GNU Make, Docker Desktop/Engine, Ollama, CUDA-capable GPU ([GPU setup](../models/gpu-setup.md))

The repository is developed on Windows and CI targets Linux. `scripts/tasks.py` is the source of truth for cross-platform commands; the Makefile is a convenience wrapper.

## Clean local setup

```powershell
git clone https://github.com/zeyadsherif2004/LabJarvis.git
cd LabJarvis
python --version
node --version
corepack enable
python scripts/tasks.py setup
.venv/Scripts/python scripts/generate_sample_data.py
python scripts/tasks.py demo
```

The repository is public (verified signed-out on 2026-09-27). Setup installs the exact Python dev lock, installs the package editable without re-resolving dependencies, and runs `pnpm install --frozen-lockfile`. The demo migrates the SQLite database and starts Uvicorn on `127.0.0.1:8000` plus Vite on `127.0.0.1:5173` in fake mode. Stop both with Ctrl+C.

On macOS/Linux the virtual-environment executable is `.venv/bin/python`; the task runner discovers it automatically after setup.

## Configuration

Defaults are safe for local fake mode. Configuration has three homes:

- **Settings page** (stored in `<data dir>/settings.json`): profile (Local,
  Hosted, Hybrid, Custom), per-stage provider/model/CPU-GPU, hands-free mode,
  high-integrity transcription, hosted permission and voice output. Applied at
  the next start; **Restart LabJarvis** does that when started with `tasks.py dev`.
- **`.env`** in the directory the API starts from (the repository root for
  `tasks.py dev`): API keys (`LABJARVIS_OPENAI_API_KEY`,
  `LABJARVIS_DEEPSEEK_API_KEY`) and any deliberate advanced override. Add only
  lines you mean to change; never commit it.
- **Built-in defaults** for everything else. `.env.example` lists every setting
  with its default; `apps/api/src/labjarvis/settings.py` is the source of truth.

Precedence is per variable: the process environment, then the saved Settings
choice, then `.env`, then the default. Avoid exporting `LABJARVIS_*` variables in
your shell or Windows environment: they override both `.env` and Settings, and
the launcher and the Settings page warn when they do.

Groups (all prefixed `LABJARVIS_`):

- runtime: `APP_ENV`, `MODEL_MODE` (`fake`/`local`/`hosted`), data/backup/database paths;
- privacy: `RETAIN_RAW_MEDIA=false`, `RETAIN_TRANSCRIPTS=true`;
- local models: speech/text/vision model names, devices (GPU), download and remote-service opt-ins;
- text interpretation: interpreter (`rules`/`llm`/`cascade`/`agreement`), thinking, timeouts, verifier model;
- hosted execution: `ALLOW_HOSTED_MODELS`, per-stage providers, named OpenAI and DeepSeek connections (`OPENAI_*`, `DEEPSEEK_*`), generic OpenAI-compatible base URLs, keys, labels, capabilities, reasoning effort;
- voice output: `SPEECH_OUTPUT` (`browser`/`kokoro`), `KOKORO_VOICE`, `KOKORO_SPEED`;
- hands-free: listening, voice-only confirmation, local-Vision live scan or hosted-Vision timed single capture, warm-up, high-integrity transcription;
- realtime voice (OpenAI), study mode;
- upload/archive/audio/image/inference limits, command and reagent thresholds, CORS/allowed hosts, logging.

Startup rejects wildcard hosts, invalid/credential-bearing URLs, non-loopback
Ollama unless opted in, non-HTTPS remote hosted URLs, hosted stages without
opt-in/key/model, undeclared modalities on compatible endpoints, invalid
extra request bodies, voice-only confirmation without listening and the second
transcriber, live scan with hosted vision, study mode in production, inverted
thresholds, and invalid bounds. `LABJARVIS_HOST`/`LABJARVIS_PORT` are declared
but not read: `tasks.py dev` always serves `127.0.0.1:8000` (API) and
`127.0.0.1:5173` (Vite).

## Local real models

1. Install Ollama from <https://ollama.com/download> and start it.
2. Run `python scripts/tasks.py models` to install the model lock and explicitly download the configured Whisper and EasyOCR weights and the Kokoro voice, and `ollama pull` the configured text model (`qwen2.5:3b`). `python scripts/setup_models.py kokoro` fetches only the voice.
3. Run `.venv/Scripts/python scripts/setup_models.py check`.
4. Run `python scripts/tasks.py dev`, open **Settings**, choose **Local** (or **Custom** for GPU), save and press **Restart LabJarvis**.
5. Check the real chain without the browser: `python scripts/tasks.py smoke-local`.

Normal API startup has model download disabled. The [model guide](../models/README.md) covers smoke fixtures, exact identifier capture, licences, and benchmarking.

## PostgreSQL profile

```powershell
$env:POSTGRES_PASSWORD="replace-with-a-local-secret"
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up --build
```

Do not put the password in Git or command screenshots. The base SQLite profile is `docker compose up --build`. Both publish only on loopback.

## Editor and hooks

Install hooks if desired:

```powershell
.venv/Scripts/pre-commit install
.venv/Scripts/pre-commit run --all-files
```

Use UTF-8, preserve existing line endings, and enable strict TypeScript/Python type diagnostics. See [CONTRIBUTING](../../CONTRIBUTING.md) for standards and the [test strategy](testing.md) before opening a change.
