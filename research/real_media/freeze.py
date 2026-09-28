"""Record the SHA-256 of a real-media manifest (and every file it names) before evaluation."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "research" / "final_report_evidence" / "manifest-hashes.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    manifest = Path(sys.argv[1])
    rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    media: dict[str, str] = {}
    for row in rows:
        for relative in [row.get("media_path"), *(row.get("frame_paths") or [])]:
            if relative:
                media[relative] = sha(ROOT / relative)
    ledger = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {}
    ledger[str(manifest).replace("\\", "/")] = {
        "frozen_at": datetime.now(UTC).isoformat(),
        "manifest_sha256": sha(manifest),
        "items": len(rows),
        "media_sha256": media,
    }
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    print(f"froze {manifest} ({len(rows)} items, {len(media)} media files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
