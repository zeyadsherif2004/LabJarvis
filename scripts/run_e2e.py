"""Run isolated LabJarvis API, web, and Playwright processes cross-platform."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import IO
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def wait_for_url(url: str, processes: tuple[subprocess.Popen[bytes], ...]) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        for process in processes:
            if process.poll() is not None:
                raise RuntimeError(f"server process exited early with status {process.returncode}")
        try:
            with urlopen(url, timeout=1) as response:  # noqa: S310 - loopback URL only
                if response.status < 500:
                    return
        except (OSError, URLError):
            time.sleep(0.1)
    raise TimeoutError(f"timed out waiting for {url}")


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
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def print_log(label: str, path: Path) -> None:
    if not path.exists():
        return
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    print(f"\n--- {label} (last 80 lines) ---", file=sys.stderr)
    print("\n".join(lines[-80:]), file=sys.stderr)


def start_process(command: list[str], env: dict[str, str], log: IO[bytes]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )


#: The zero-touch journey runs against an API started with the one-switch
#: hands-free profile (fake models), in its own pass with its own database.
HANDS_FREE_ENV = {
    "LABJARVIS_HANDS_FREE_PROFILE": "true",
    "LABJARVIS_E2E_PROFILE": "hands-free",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--screenshots",
        action="store_true",
        help="run the deterministic screenshot journey instead of the E2E suite",
    )
    parser.add_argument(
        "--profile",
        choices=("all", "default", "hands-free"),
        default="all",
        help="which server configuration(s) to run the suite against",
    )
    arguments, playwright_arguments = parser.parse_known_args()
    pnpm = shutil.which("pnpm.cmd" if os.name == "nt" else "pnpm")
    if pnpm is None:
        print("pnpm is required to run browser tests", file=sys.stderr)
        return 2
    if arguments.screenshots:
        return run_pass(pnpm, {}, playwright_arguments, screenshots=True)
    status = 0
    if arguments.profile in {"all", "default"}:
        status = run_pass(pnpm, {}, playwright_arguments, screenshots=False)
    if status == 0 and arguments.profile in {"all", "hands-free"}:
        status = run_pass(pnpm, HANDS_FREE_ENV, playwright_arguments, screenshots=False)
    return status


def run_pass(
    pnpm: str,
    extra_env: dict[str, str],
    playwright_arguments: list[str],
    *,
    screenshots: bool,
) -> int:
    api_port = free_port()
    web_port = free_port()
    with tempfile.TemporaryDirectory(prefix="labjarvis-e2e-") as temp_name:
        temp = Path(temp_name)
        api_log_path = temp / "api.log"
        web_log_path = temp / "web.log"
        env = os.environ.copy()
        env.update(
            {
                "LABJARVIS_APP_ENV": "test",
                "LABJARVIS_MODEL_MODE": "fake",
                # The hands-free journey confirms a measurement by spoken
                # read-back, which requires the second (verifier) transcription.
                "LABJARVIS_HIGH_INTEGRITY_TRANSCRIPTION_ENABLED": "true",
                "LABJARVIS_HIGH_INTEGRITY_TRANSCRIPTION_MODEL_NAME": "fake-speech-verification-v1",
                "LABJARVIS_DATA_DIR": str(temp / "data"),
                "LABJARVIS_DATABASE_URL": f"sqlite+pysqlite:///{(temp / 'e2e.db').as_posix()}",
                "LABJARVIS_CORS_ORIGINS": json.dumps([f"http://127.0.0.1:{web_port}"]),
                "LABJARVIS_API_PROXY": f"http://127.0.0.1:{api_port}",
                "LABJARVIS_WEB_PORT": str(web_port),
                "PLAYWRIGHT_BASE_URL": f"http://127.0.0.1:{web_port}",
                "PYTHONUNBUFFERED": "1",
                **extra_env,
            }
        )
        # The API no longer creates its own schema: Alembic owns it, and
        # starting against an unmigrated database is a deliberate hard failure.
        # The throwaway E2E database therefore has to be migrated first, exactly
        # as `scripts/tasks.py dev` and the container entry point do.
        subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                "apps/api/alembic.ini",
                "upgrade",
                "head",
            ],
            cwd=ROOT,
            env=env,
            check=True,
        )
        api_command = [
            sys.executable,
            "-m",
            "uvicorn",
            "labjarvis.api.app:create_app",
            "--factory",
            "--app-dir",
            "apps/api/src",
            "--host",
            "127.0.0.1",
            "--port",
            str(api_port),
        ]
        web_command = [pnpm, "--dir", "apps/web", "dev"]

        with api_log_path.open("wb") as api_log, web_log_path.open("wb") as web_log:
            api = start_process(api_command, env, api_log)
            web = start_process(web_command, env, web_log)
            try:
                wait_for_url(f"http://127.0.0.1:{api_port}/health/ready", (api, web))
                wait_for_url(f"http://127.0.0.1:{web_port}/", (api, web))
                command = [pnpm, "--dir", "apps/web", "exec", "playwright", "test"]
                command.extend(playwright_arguments)
                if screenshots:
                    command.extend(
                        ["--config", "playwright.screenshots.config.ts"]
                    )
                result = subprocess.run(
                    command,
                    cwd=ROOT,
                    env=env,
                    check=False,
                )
            except (RuntimeError, TimeoutError) as exc:
                print(str(exc), file=sys.stderr)
                result = subprocess.CompletedProcess([], 1)
            finally:
                stop_process(web)
                stop_process(api)

        if result.returncode != 0:
            print_log("API", api_log_path)
            print_log("web", web_log_path)
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
