"""Fresh-copy readiness check: everything an examiner can reproduce.

Copies exactly the files a commit of this working tree would contain (tracked
plus untracked-but-not-ignored) into a temporary directory, then runs every
deterministic gate there - lint, types, backend/research/frontend tests, E2E,
production build, held-out split integrity and secret scan - with an empty
LabJarvis environment. Writes ``readiness.json`` (machine-readable) and
``readiness.md`` (human-readable summary) beneath ignored local
``output/readiness/``.

By default the existing ``.venv`` interpreter is reused (``--fresh-venv`` builds
a new one from the lock files, which takes several minutes and needs network).

    python scripts/readiness.py [--fresh-venv] [--skip-e2e]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "readiness"


def files_to_copy() -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT, check=True, capture_output=True,
    ).stdout  # fmt: skip
    return [item.decode() for item in listed.split(b"\0") if item]


def clean_environment() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("LABJARVIS_")}
    env["PYTHONUNBUFFERED"] = "1"
    return env


def gate(name: str, command: list[str], cwd: Path, env: dict[str, str]) -> dict[str, object]:
    started = time.monotonic()
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")  # fmt: skip
    tail = "\n".join((result.stdout + result.stderr).strip().splitlines()[-6:])
    return {
        "gate": name,
        "command": " ".join(command),
        "exit": result.returncode,
        "status": "PASS" if result.returncode == 0 else "FAIL",
        "seconds": round(time.monotonic() - started, 1),
        "tail": tail,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fresh-venv", action="store_true")
    parser.add_argument("--skip-e2e", action="store_true")
    parser.add_argument("--keep", action="store_true", help="keep the temporary copy")
    args = parser.parse_args()
    pnpm = shutil.which("pnpm.cmd" if os.name == "nt" else "pnpm")
    if pnpm is None:
        print("pnpm is required", file=sys.stderr)
        return 2
    temp = Path(tempfile.mkdtemp(prefix="labjarvis-readiness-"))
    copied = files_to_copy()
    for relative in copied:
        source = ROOT / relative
        if source.is_file():
            target = temp / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    env = clean_environment()
    records: list[dict[str, object]] = []
    # The secret scanner enumerates files through Git, so the copy gets its own
    # throwaway index (staged only; nothing is committed anywhere).
    records.append(gate("git index", ["git", "init", "-q"], temp, env))
    records.append(gate("git stage", ["git", "add", "-A"], temp, env))
    if args.fresh_venv:
        records.append(gate("venv", [sys.executable, "-m", "venv", ".venv"], temp, env))
        python = str(temp / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
        records.append(gate("install", [python, "-m", "pip", "install", "-q", "-r", "apps/api/requirements-dev.lock"], temp, env))
    else:
        python = sys.executable
    env["PYTHONPATH"] = os.pathsep.join([str(temp / "apps" / "api" / "src"), str(temp)])
    records.append(gate("pnpm install", [pnpm, "install", "--frozen-lockfile"], temp, env))
    steps: list[tuple[str, list[str]]] = [
        ("ruff", [python, "-m", "ruff", "check", "apps/api", "scripts", "research"]),
        ("mypy strict", [python, "-m", "mypy", "apps/api/src", "scripts", "--cache-dir", str(temp / ".mypy_cache")]),
        ("backend tests + branch coverage", [python, "-m", "pytest", "apps/api/tests", "-p", "no:cacheprovider",
                                             "--cov=labjarvis", "--cov-branch", "--cov-report=term", "--cov-fail-under=85"]),
        ("research tests", [python, "-m", "pytest", "research/post_review", "research/analysis",
                            "research/experiments", "research/study", "-p", "no:cacheprovider"]),
        ("held-out split integrity", [python, "-m", "research.interpreter.check_splits"]),
        ("eslint", [pnpm, "--dir", "apps/web", "lint"]),
        ("typescript", [pnpm, "--dir", "apps/web", "typecheck"]),
        ("typescript e2e", [pnpm, "--dir", "apps/web", "exec", "tsc", "--noEmit", "-p", "tsconfig.e2e.json"]),
        ("vitest", [pnpm, "--dir", "apps/web", "test", "--run"]),
        ("production build", [pnpm, "--dir", "apps/web", "build"]),
        ("secret scan", [python, "scripts/scan_secrets.py"]),
    ]  # fmt: skip
    if not args.skip_e2e:
        steps.append(("playwright e2e", [python, "scripts/run_e2e.py"]))
    for name, command in steps:
        print(f"-> {name}", flush=True)
        records.append(gate(name, command, temp, env))
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    payload = {
        "created_at": datetime.now(UTC).isoformat(),
        "head": head,
        "working_tree_files": len(copied),
        "note": "copy of the committable working tree (tracked + untracked, not ignored); empty LABJARVIS_* environment",
        "fresh_venv": args.fresh_venv,
        "overall": "PASS" if all(r["status"] == "PASS" for r in records) else "FAIL",
        "gates": records,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "readiness.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    lines = [
        f"# Fresh-copy readiness - {payload['created_at'][:19]}Z",
        "",
        f"HEAD `{head}` plus the uncommitted working tree ({len(copied)} files). Overall: **{payload['overall']}**.",
        "",
        "| Gate | Result | Seconds | Last output |",
        "|---|---|---:|---|",
    ]
    for record in records:
        last = str(record["tail"]).splitlines()[-1] if record["tail"] else ""
        lines.append(f"| {record['gate']} | {record['status']} | {record['seconds']} | {last.replace('|', '/')[:120]} |")
    (OUT / "readiness.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    if not args.keep:
        shutil.rmtree(temp, ignore_errors=True)
    return 0 if payload["overall"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
