# Troubleshooting

## Setup cannot find pnpm

Run `corepack enable`, confirm `pnpm --version` is 11.9.0-compatible, then rerun setup. On restricted Windows installations, use a terminal with permission to activate Corepack; do not replace the lock with npm.

## `.venv` uses the wrong Python

Delete/recreate only your local `.venv` with Python 3.12, then rerun `python scripts/tasks.py setup`. The directory is ignored. Do not edit generated lock files to accommodate another interpreter.

## Startup or a task fails with a settings `ValidationError`

Settings are validated at startup, and **process environment variables take
precedence over `.env`**. A `LABJARVIS_*` variable left in your user or system
environment (for example `LABJARVIS_MODEL_MODE=hosted` without the hosted model
names) makes `dev`, `evaluate` and the scripts fail or behave unexpectedly even
when `.env` is correct. List them and clear the stale ones:

```powershell
Get-ChildItem Env:LABJARVIS_*
Remove-Item Env:LABJARVIS_MODEL_MODE   # current shell only; also remove it from the Windows user environment
```

The test suites are unaffected: they strip ambient `LABJARVIS_*` variables.
`tasks.py dev` prints a warning naming any inherited `LABJARVIS_*` variable, and
**Settings -> Configuration warnings** lists any that override `.env` or a saved
choice. Keep keys in `.env` and choices in Settings instead.

## Startup stops with "the configuration saved from the LabJarvis Settings page ... is not valid"

The saved choice no longer validates in the current environment (for example a
hosted stage whose API key was removed from `.env`). LabJarvis refuses to start
on different models rather than silently changing them. Restore the key, or
delete or rename `<data dir>/settings.json` (default `.labjarvis/settings.json`)
and choose again in Settings.

## Settings says "restart it manually" or refuses to save

**Restart LabJarvis** needs the supervisor that `python scripts/tasks.py dev`
(or `hands-free`) provides; with plain `uvicorn` or the containers, stop and
start LabJarvis yourself. Saving and restarting are accepted only from the
computer LabJarvis runs on (a request through the nginx container is refused).

## LabJarvis speaks with the robotic browser voice

Kokoro is chosen but not usable, so the browser voice is speaking instead
(**Settings -> Voice output** says why). Install it with
`python scripts/setup_models.py kokoro` (or `tasks.py models`), then restart. If
Kokoro fails mid-reply the rest is spoken by the browser voice and Kokoro is
retried after 30 seconds. Check `/health/ready` -> `speech_output`.

## API reports a database/open-file error

- Ensure `.labjarvis` is writable and no stale server owns the SQLite file.
- Run `python scripts/tasks.py migrate`.
- For development data only, `python scripts/tasks.py clean` removes the local application directory; this is destructive to local notebook data.
- For PostgreSQL, check the container health and URL/password; never paste the password into an issue.

## Browser shows API unavailable

Use `python scripts/tasks.py demo` or `dev`, which starts both processes. Check <http://127.0.0.1:8000/health/ready>. If ports are occupied, stop the old process or configure coordinated host/proxy/CORS values. A raw `pnpm --dir apps/web e2e` does not start test servers; use `python scripts/tasks.py e2e`.

## Microphone or camera is denied

Grant permission to the loopback origin, close other exclusive device users, and retry **Test microphone/camera**. Typed commands and image-file fixtures remain available. Some browsers require a secure context; `localhost`/loopback is treated as trustworthy, but remote HTTP is not supported.

## Audio command is rejected

The backend accepts bounded browser media and the configured adapter must support the content type. Inspect the visible Problem Details code and correlation ID, not private body logs. In fake mode, automated fixtures use explicit fake media; a real microphone recording is for local mode.

## Local readiness says model unavailable

Run:

```powershell
.venv/Scripts/python scripts/setup_models.py check
ollama list
```

If dependencies are missing, rerun `python scripts/tasks.py models`. If Ollama is not running, start its local service. Confirm the configured tag exists. Downloads are intentionally not triggered by normal startup.

## Model inference times out or exhausts memory

Use CPU `int8` for `small.en`, keep inference concurrency at one, close other model processes, or benchmark a smaller configured candidate. Do not simply raise the timeout and describe the result as acceptable; record latency/RSS on target hardware. GPU mode needs a CUDA build of PyTorch and the CUDA 12 cuBLAS wheel, which the default locks do not install; follow [GPU setup](../models/gpu-setup.md). A "GPU runtime libraries ... missing" error means those libraries are absent: set `LABJARVIS_SPEECH_DEVICE=cpu` or install them. Set `LABJARVIS_WARM_MODELS_ON_STARTUP=true` to avoid a 10-12 s first call.

## EasyOCR download/readiness issues

The configured cache is `.labjarvis/models/easyocr`. `python scripts/tasks.py models` explicitly allows the detector and `english_g2` downloads. Verify disk/network access and the upstream checksum record; do not commit cache files.

## A live reagent scan keeps changing or shows HTTP 503

This mode is available when Vision runs on this computer. With hosted Vision,
"check reagent" instead captures one cropped frame after a spoken countdown;
the image leaves this computer only for that deliberate check.
With Local Vision, **Local camera check** also offers **Timed single frame**.
Choose it before gloving up if the moving scan is distracting; the choice is
saved in this browser. It waits for the spoken countdown and sends one frame to
local OCR, so a single mistaken reading may still need manual correction or a
retake. Switch back to **Live scan** to compare several readings again.

- Keep only the reagent name inside the camera's yellow aim box. The camera sends that cropped area to EasyOCR, so background text outside it should not affect the reading.
- A probe can receive `503 MODEL_UNAVAILABLE` while the single inference slot is busy with speech or another model call. The runner waits briefly, and the browser skips a failed probe rather than treating it as unreadable label text. If all probes fail, the scan stops without recording a reading.
- The current `stable-asymmetric-v3` rule tolerates two stray probes while looking for three matching readings. A non-matching result needs the same recognised text three times; different OCR guesses must not settle as a wrong label. The selected frames are read again by the unanimous verifier, and the operator still confirms the pending reading.
- EasyOCR can consistently misread handwriting or a blurred label. If the observed text is wrong, improve light and framing, retry, or use an image upload and the visible-text correction control. Treat a displayed `MATCH` as a label-text comparison, not a chemical identity check.

## Hands-free listening or voice-only confirmation does nothing

- Readiness must report `hands_free_listening` (and `voice_only_confirmation`) as enabled; both are off by default and need a restart (**Settings -> Hands-free mode** turns on the whole profile).
- Voice-only confirmation needs `LABJARVIS_HIGH_INTEGRITY_TRANSCRIPTION_ENABLED=true` with a model name, or startup fails.
- Start the listener with **Start hands-free listening** (or **Tap once to start hands-free** with autostart); it pauses while LabJarvis speaks. Outside a session it switches off after 10 minutes without speech; while a session is open it stays on.
- Every command and reply must start with the wake phrase ("Jarvis, ..."); anything else is ignored by design. Replies must restate the value ("Jarvis, confirm 10 millilitres").
- If the listener cannot start, check that `apps/web/public/vad/` exists (copied by `pnpm --dir apps/web dev`/`build` via `apps/web/scripts/copy-vad-assets.mjs`).

## Tests fail because a real model is absent

Default tests must not need real models. Model smoke tests are intentionally skipped unless `LABJARVIS_RUN_MODEL_TESTS=1` and external WAV/JPEG paths are supplied. If an ordinary suite tries to download or connect, treat it as a regression.

## Docker commands fail

`docker compose config --quiet` works without starting containers, but builds/runs require a running Docker daemon. Start Docker Desktop/Engine. SQLite/fake mode remains the supported no-Docker path.

## A 409/428 response appears

- `428 IF_MATCH_REQUIRED`: send the current numeric session/protocol revision in `If-Match`.
- `428 IDEMPOTENCY_KEY_REQUIRED`: send a nonblank unique key for command submission.
- `409 ...CONFLICT`: reload the latest object; do not blindly retry stale state.
- `409 ...RESOLVED`: the pending decision was already finalized.

All problem responses include a safe correlation ID for matching structured operational logs.
