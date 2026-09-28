"""Cross-platform task runner behind the optional Makefile shortcuts."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from supervisor import RestartSupervisor

ROOT = Path(__file__).resolve().parents[1]


def venv_python() -> Path:
    relative = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    return ROOT / ".venv" / relative


def pnpm_command() -> str:
    executable = shutil.which("pnpm.cmd" if os.name == "nt" else "pnpm")
    if executable is None:
        raise RuntimeError("pnpm is required; enable Corepack or install pnpm 11.9.0")
    return executable


def run(command: list[str], *, environment: dict[str, str] | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, env=environment, check=True)


def require_environment() -> tuple[str, str]:
    python = venv_python()
    if not python.is_file():
        raise RuntimeError(".venv is missing; run 'python scripts/tasks.py setup' first")
    return str(python), pnpm_command()


def setup() -> None:
    python = venv_python()
    if not python.is_file():
        run([sys.executable, "-m", "venv", str(ROOT / ".venv")])
    run([str(python), "-m", "pip", "install", "--upgrade", "pip"])
    run([str(python), "-m", "pip", "install", "-r", "apps/api/requirements-dev.lock"])
    run([str(python), "-m", "pip", "install", "-e", "apps/api", "--no-deps"])
    run([pnpm_command(), "install", "--frozen-lockfile"])


def migrate() -> None:
    python, _ = require_environment()
    run([python, "-m", "alembic", "-c", "apps/api/alembic.ini", "upgrade", "head"])


def stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def warn_about_inherited_configuration(environment: dict[str, str]) -> None:
    """Say loudly when the shell, not .env or Settings, is configuring LabJarvis.

    Every Restart from Settings reuses this launcher's environment, and an
    environment variable outranks both the saved Settings choices and .env. A
    key or setting inherited here therefore cannot be changed by editing .env
    or by Settings until LabJarvis is started from a shell without it.
    """

    inherited = sorted(
        name
        for name, value in environment.items()
        if name.upper().startswith("LABJARVIS_")
        and value.strip()
    )
    if inherited:
        print(
            "WARNING: these LabJarvis variables are set in this shell and override .env and "
            "the Settings page for as long as this launcher runs (including Restart from "
            "Settings): " + ", ".join(inherited),
            file=sys.stderr,
            flush=True,
        )


def dev(
    *,
    fake_demo: bool = False,
    extra_environment: dict[str, str] | None = None,
    on_ready: Callable[[], None] | None = None,
) -> None:
    """Run the API and web app; the API is supervised so Settings can restart it.

    The only thing the supervisor ever restarts is the fixed API command below,
    and only when the API itself asked (see scripts/supervisor.py). The web dev
    server keeps running across an API restart.
    """

    python, pnpm = require_environment()
    migrate()
    environment = os.environ.copy()
    # Checked before the launcher adds its own variables: only what the shell
    # passed in is a surprise to the operator.
    warn_about_inherited_configuration(environment)
    if fake_demo:
        environment["LABJARVIS_MODEL_MODE"] = "fake"
        # The demo is the deterministic fixture profile whatever was saved.
        environment["LABJARVIS_IGNORE_SAVED_SETTINGS"] = "true"
    environment.update(extra_environment or {})
    supervisor = RestartSupervisor(ROOT / ".labjarvis" / "supervisor")
    environment.update(supervisor.start())
    api_command = [
        python,
        "-m",
        "uvicorn",
        "labjarvis.api.app:create_app",
        "--factory",
        "--app-dir",
        "apps/api/src",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
    ]
    api = subprocess.Popen(api_command, cwd=ROOT, env=environment)
    web = subprocess.Popen([pnpm, "--dir", "apps/web", "dev"], cwd=ROOT, env=environment)
    print("LabJarvis API: http://127.0.0.1:8000", flush=True)
    print("LabJarvis web: http://127.0.0.1:5173", flush=True)
    if on_ready is not None:
        on_ready()
    try:
        while True:
            while api.poll() is None and web.poll() is None:
                time.sleep(0.2)
            if web.poll() is None and supervisor.consume_restart_request():
                print("Restarting the LabJarvis API with the saved configuration...", flush=True)
                api = subprocess.Popen(api_command, cwd=ROOT, env=environment)
                continue
            failed = api if api.poll() is not None else web
            raise RuntimeError(f"development server exited with status {failed.returncode}")
    except KeyboardInterrupt:
        return
    finally:
        stop_process(web)
        stop_process(api)
        supervisor.stop()


KIOSK_URL = "http://127.0.0.1:5173/?kiosk=1"
#: Chromium switch that lets a page play audio without a user gesture. It is
#: passed only to a dedicated browser profile opened on the loopback address.
AUTOPLAY_FLAG = "--autoplay-policy=no-user-gesture-required"


def find_chromium() -> str | None:
    """Chrome or Edge (both Chromium, both honour the autoplay switch)."""

    for name in ("chrome", "google-chrome", "google-chrome-stable", "chromium", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    candidates = [
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"))
        / "Microsoft/Edge/Application/msedge.exe",
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    ]
    return next((str(path) for path in candidates if path.is_file()), None)


def hands_free() -> None:
    """Kiosk launch: the hands-free profile, then Chrome in app mode on localhost.

    Starts the API and web app with LABJARVIS_HANDS_FREE_PROFILE=true (startup
    is refused if a required local model is missing), then opens a Chromium
    browser in app mode with the autoplay flag, using a dedicated profile under
    .labjarvis/kiosk-profile, on http://127.0.0.1 only. The page confirms on
    screen that kiosk mode is active; if the flag is not honoured it falls
    back to the one-tap prompt. Microphone and camera permission is granted
    once in that profile (before gloving up) and remembered for localhost.
    """

    browser = find_chromium()
    profile = ROOT / ".labjarvis" / "kiosk-profile"

    def open_browser() -> None:
        if browser is None:
            print(
                "No Chrome or Edge found: open " + KIOSK_URL + " yourself; the page will "
                "show the one-tap start prompt instead of starting by itself.",
                flush=True,
            )
            return
        time.sleep(4)  # let Vite finish its first build
        profile.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(
            [browser, f"--app={KIOSK_URL}", AUTOPLAY_FLAG, f"--user-data-dir={profile}"],
            cwd=ROOT,
        )
        print(f"Kiosk: {Path(browser).name} app window on {KIOSK_URL} ({AUTOPLAY_FLAG})", flush=True)

    dev(extra_environment={"LABJARVIS_HANDS_FREE_PROFILE": "true"}, on_ready=open_browser)


def test() -> None:
    python, pnpm = require_environment()
    run([python, "-m", "pytest", "apps/api/tests"])
    # Research and evaluation code produces report evidence, so it is tested too.
    run(
        [
            python,
            "-m",
            "pytest",
            "research/post_review",
            "research/analysis",
            "research/experiments",
            "research/study",
            "-p",
            "no:cacheprovider",
        ]
    )
    run([pnpm, "--dir", "apps/web", "test", "--run"])


def lint() -> None:
    python, pnpm = require_environment()
    run([python, "-m", "ruff", "check", "apps/api", "scripts", "research"])
    run([pnpm, "--dir", "apps/web", "lint"])


def typecheck() -> None:
    python, pnpm = require_environment()
    run([python, "-m", "mypy", "apps/api/src", "scripts"])
    run([pnpm, "--dir", "apps/web", "typecheck"])
    run([pnpm, "--dir", "apps/web", "exec", "tsc", "--noEmit", "-p", "tsconfig.e2e.json"])


def build() -> None:
    """Produce the static frontend bundle the container image serves.

    Named separately from `test` because a bundle that fails to build is a
    release blocker even when every test passes, and the verification matrix
    records it as its own line.
    """

    _, pnpm = require_environment()
    run([pnpm, "--dir", "apps/web", "build"])


def e2e() -> None:
    python, _ = require_environment()
    run([python, "scripts/run_e2e.py"])


def screenshots() -> None:
    python, _ = require_environment()
    run([python, "scripts/run_e2e.py", "--screenshots"])


def evaluate() -> None:
    python, _ = require_environment()
    run(
        [
            python,
            "-m",
            "labjarvis.evaluation.run",
            "--config",
            "evaluation/configs/development.yaml",
        ]
    )


def evaluate_safety() -> None:
    """Measure the deterministic safety properties of the hybrid architecture.

    Offline and reproducible: no model runs and nothing is billed.
    """

    python, _ = require_environment()
    run([python, "-m", "labjarvis.evaluation.safety"])


def test_models() -> None:
    python, _ = require_environment()
    run([python, "-m", "pytest", "apps/api/tests/model_smoke", "-m", "model"])


def test_hosted_smoke() -> None:
    """Run the opt-in live hosted smoke test.

    This makes real, paid provider requests. It stays skipped unless
    LABJARVIS_RUN_HOSTED_SMOKE=1 and a hosted key are both configured, and it is
    never part of `test`, CI, or pre-commit.
    """

    python, _ = require_environment()
    if os.environ.get("LABJARVIS_RUN_HOSTED_SMOKE") != "1":
        print(
            "Refusing to run: set LABJARVIS_RUN_HOSTED_SMOKE=1 to authorise paid "
            "hosted provider requests.",
            file=sys.stderr,
        )
        raise RuntimeError("hosted smoke test not authorised")
    run(
        [
            python,
            "-m",
            "pytest",
            "apps/api/tests/model_smoke/test_hosted_smoke.py",
            "-m",
            "hosted_smoke",
            "-s",
        ]
    )


def test_realtime_smoke() -> None:
    """Run the opt-in live realtime grant smoke test.

    This makes a real, paid provider request. It proves only that a short-lived
    grant can be minted and is scoped to the tool allowlist; spoken interaction,
    barge-in, and reconnection happen in a browser and are covered by the manual
    boundary in docs/architecture/decisions/0014-spoken-confirmation-requires-fresh-human-audio.md.
    """

    python, _ = require_environment()
    if os.environ.get("LABJARVIS_RUN_REALTIME_SMOKE") != "1":
        print(
            "Refusing to run: set LABJARVIS_RUN_REALTIME_SMOKE=1 to authorise a paid "
            "realtime provider request.",
            file=sys.stderr,
        )
        raise RuntimeError("realtime smoke test not authorised")
    run(
        [
            python,
            "-m",
            "pytest",
            "apps/api/tests/model_smoke/test_realtime_smoke.py",
            "-m",
            "hosted_smoke",
            "-s",
        ]
    )


def models() -> None:
    python, _ = require_environment()
    run([python, "-m", "pip", "install", "-r", "apps/api/requirements-models.lock"])
    run([python, "-m", "pip", "install", "-e", "apps/api", "--no-deps"])
    run([python, "scripts/setup_models.py", "download"])


def lock() -> None:
    python, _ = require_environment()
    variants: tuple[tuple[str | None, str], ...] = (
        (None, "requirements.lock"),
        ("dev", "requirements-dev.lock"),
        ("models", "requirements-models.lock"),
        ("postgres", "requirements-postgres.lock"),
    )
    for extra, output in variants:
        command = [
            python,
            "-m",
            "piptools",
            "compile",
            "apps/api/pyproject.toml",
            "--output-file",
            f"apps/api/{output}",
            "--strip-extras",
        ]
        if extra is not None:
            command.extend(["--extra", extra])
        run(command)


def security() -> None:
    python, pnpm = require_environment()
    run([python, "scripts/scan_secrets.py"])
    run([python, "-m", "pip_audit"])
    run([pnpm, "audit", "--audit-level", "high"])


def clean() -> None:
    targets = (
        ROOT / ".coverage",
        ROOT / "htmlcov",
        ROOT / "output",
        ROOT / "apps" / "web" / "dist",
        ROOT / ".labjarvis",
    )
    for target in targets:
        resolved = target.resolve()
        if not resolved.is_relative_to(ROOT.resolve()):
            raise RuntimeError(f"refusing to clean outside repository: {resolved}")
        if resolved.is_dir():
            shutil.rmtree(resolved)
        elif resolved.is_file():
            resolved.unlink()
    results = ROOT / "evaluation" / "results"
    for child in results.iterdir():
        if child.name == ".gitkeep":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def demo() -> None:
    dev(fake_demo=True)


def ci() -> None:
    """Everything deterministic an examiner can reproduce: no models, no network."""

    lint()
    typecheck()
    test()
    build()
    e2e()
    python, _ = require_environment()
    run([python, "scripts/scan_secrets.py"])


def smoke_local() -> None:
    """Real local models (Whisper, Ollama, EasyOCR) through the production service.

    Needs the models dependency group, pulled weights and a running Ollama; no
    network and no cost. Fails unless every scenario reaches its expected result.
    """

    python, _ = require_environment()
    run([python, "-m", "research.real_media.run_chain", "--scenario", "demo"])


def smoke_hosted() -> None:
    """Paid live smoke of configured hosted providers, capped by the spend ledger."""

    python, _ = require_environment()
    if os.environ.get("LABJARVIS_RUN_HOSTED_SMOKE") != "1":
        print(
            "Refusing to run: set LABJARVIS_RUN_HOSTED_SMOKE=1 to authorise paid requests.",
            file=sys.stderr,
        )
        raise RuntimeError("hosted smoke not authorised")
    run([python, "scripts/hosted_smoke.py"])


def readiness() -> None:
    python, _ = require_environment()
    run([python, "scripts/readiness.py"])


COMMANDS: dict[str, Callable[[], None]] = {
    "setup": setup,
    "dev": dev,
    "hands-free": hands_free,
    "test": test,
    "lint": lint,
    "typecheck": typecheck,
    "build": build,
    "e2e": e2e,
    "screenshots": screenshots,
    "evaluate": evaluate,
    "evaluate-safety": evaluate_safety,
    "test-models": test_models,
    "ci": ci,
    "smoke-local": smoke_local,
    "smoke-hosted": smoke_hosted,
    "readiness": readiness,
    "test-hosted-smoke": test_hosted_smoke,
    "test-realtime-smoke": test_realtime_smoke,
    "models": models,
    "lock": lock,
    "migrate": migrate,
    "security": security,
    "demo": demo,
    "clean": clean,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=tuple(COMMANDS))
    arguments = parser.parse_args()
    try:
        COMMANDS[arguments.command]()
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        return exc.returncode if isinstance(exc, subprocess.CalledProcessError) else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
