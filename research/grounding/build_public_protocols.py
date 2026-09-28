"""Fetch the grounding *test* protocols from OpenWetWare at pinned revisions.

The test split must be independent of every protocol the retriever was tuned
on (the three September protocols form the development split). These four
community protocols are CC BY-SA 3.0 on OpenWetWare. Steps are the page's own
top-level numbered instructions with wiki markup removed - no rewording.

    python -m research.grounding.build_public_protocols
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

API = "https://openwetware.org/mediawiki/api.php"
OUT = Path(__file__).resolve().parent / "protocols-test.json"

#: title -> (pinned revision, the section(s) whose numbered steps are used)
PAGES: dict[str, tuple[int, tuple[str, ...]]] = {
    "Agarose gel electrophoresis": (819607, ("Casting Gels",)),
    "LB": (876067, ("Protocol", "For plates")),
    "Ethanol precipitation of nucleic acids": (611420, ("Eppendorf Protocol",)),
    "Preparing chemically competent cells": (1112593, ("Preparation",)),
}


def fetch(title: str, revision: int) -> dict[str, Any]:
    """Fetch the current page and refuse it unless it is still the pinned revision.

    (Requests by ``oldid`` are refused by the wiki's API gateway.)
    """

    query = urllib.parse.urlencode(
        {"action": "parse", "page": title, "prop": "wikitext|revid", "format": "json"}
    )
    request = urllib.request.Request(f"{API}?{query}", headers={"User-Agent": "LabJarvis-research/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        parsed = dict(json.load(response)["parse"])
    if int(parsed.get("revid", 0)) != revision:
        raise SystemExit(f"{title}: page changed from pinned revision {revision}")
    return parsed


def clean(text: str) -> str:
    text = re.sub(r"\[\[(?:Image|File):[^\]]*\]\]", " ", text)
    text = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"\[https?://\S+\s([^\]]*)\]", r"\1", text)
    text = re.sub(r"--\s*\S*\s*\d{2}:\d{2}.*$", "", text)
    text = re.sub(r"<[^>]+>|'''|''|\{\{[^}]*\}\}", "", text)
    text = html.unescape(text).replace("&deg;", "°")
    return " ".join(text.split())


def sections(wikitext: str) -> dict[str, list[str]]:
    current = "_top"
    found: dict[str, list[str]] = {current: []}
    for line in wikitext.splitlines():
        heading = re.match(r"^=+\s*(.*?)\s*=+\s*$", line)
        if heading:
            current = heading.group(1)
            found.setdefault(current, [])
            continue
        found[current].append(line)
    return found


def build() -> list[dict[str, Any]]:
    protocols = []
    for title, (revision, wanted) in PAGES.items():
        page = fetch(title, revision)
        wikitext = page["wikitext"]["*"]
        by_section = sections(wikitext)
        steps = []
        for section in wanted:
            for line in by_section.get(section, []):
                if line.startswith("#") and not line.startswith(("#*", "#:")):
                    instruction = clean(line.lstrip("#"))
                    if instruction:
                        steps.append({"section": section, "instruction": instruction})
        slug = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")
        source_lines = [clean(line.lstrip("#*:")) for line in wikitext.splitlines()]
        source_text = "\n".join(line for line in source_lines if len(line) >= 12)
        protocols.append(
            {
                "id": f"oww-{slug}",
                "title": title,
                "authors": ["OpenWetWare contributors"],
                "url": f"https://openwetware.org/w/index.php?oldid={revision}",
                "revision": revision,
                "license": "CC BY-SA 3.0",
                "retrieved_at": "2026-09-26",
                "content_sha256": hashlib.sha256(wikitext.encode("utf-8")).hexdigest(),
                "source_text": source_text,
                "steps": [
                    {
                        "id": f"{slug}-{index}",
                        "number": index,
                        "title": step["section"],
                        "instruction": step["instruction"],
                        "duration_seconds": None,
                    }
                    for index, step in enumerate(steps, 1)
                ],
            }
        )
    return protocols


def main() -> int:
    protocols = build()
    OUT.write_text(json.dumps(protocols, indent=2, ensure_ascii=False), encoding="utf-8")
    for protocol in protocols:
        print(protocol["id"], len(protocol["steps"]), "steps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
