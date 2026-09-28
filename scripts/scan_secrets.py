"""Fail when tracked text contains a small set of high-confidence secret signatures."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "OpenAI-style secret": re.compile(r"\bsk-[A-Za-z0-9]{32,}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
}
TEXT_SUFFIXES = {
    "",
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsonl",
    ".md",
    ".py",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}


def tracked_files() -> tuple[Path, ...]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return tuple(ROOT / item.decode() for item in result.stdout.split(b"\0") if item)


def live_credentials() -> dict[str, str]:
    """Secrets present in this shell, so the scan can prove none was written to disk.

    Values are compared in memory only and never printed.
    """

    import os

    return {
        name: value.strip()
        for name, value in os.environ.items()
        if len(value.strip()) >= 16
        and any(marker in name.upper() for marker in ("KEY", "TOKEN", "SECRET"))
    }


def main() -> int:
    findings: list[str] = []
    credentials = live_credentials()
    for path in tracked_files():
        if path.suffix.casefold() not in TEXT_SUFFIXES or not path.is_file():
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        for line_number, line in enumerate(content.splitlines(), start=1):
            for label, pattern in PATTERNS.items():
                if pattern.search(line):
                    findings.append(f"{path.relative_to(ROOT)}:{line_number}: {label}")
            for name, value in credentials.items():
                if value in line:
                    findings.append(f"{path.relative_to(ROOT)}:{line_number}: value of ${name}")
    if findings:
        print("Potential committed secrets detected:")
        print("\n".join(findings))
        return 1
    print("No high-confidence secret signatures found in tracked text files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
