"""Build the held-out human-written paraphrase set from public sources.

Implements, verbatim, the inclusion and gold-labelling rules predeclared in
``docs/evaluation/model-selection-protocol.md``. Nothing here generates,
translates or rewrites an utterance: every transcript is the original human
wording from its source. Selection within a (source, intent) cell is by SHA-256
order of the text, never by hand.

Usage::

    python -m research.interpreter.build_heldout --fetch --sources <dir>
    python -m research.interpreter.build_heldout --sources <dir>

``--fetch`` downloads the public source files into ``<dir>`` (not committed).
The output records the SHA-256 of every source file and of the manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "research" / "data" / "heldout" / "human-paraphrases.jsonl"
PROVENANCE = ROOT / "research" / "data" / "heldout" / "SOURCES.json"
CELL_CAP = 12
#: Deviation D1 (pre-results): timer cells are not capped at 12, because timer
#: durations are the main human-written source of safety-critical numbers.
TIMER_CAP = 40
CONVERSION_COUNT = 12
OOS_COUNT = 15

SOURCES = {
    "clinc150": {
        "url": "https://raw.githubusercontent.com/clinc/oos-eval/master/data/data_full.json",
        "file": "clinc_data_full.json",
        "licence": "CC BY 3.0",
        "citation": "Larson et al. (2019), EMNLP-IJCNLP, An Evaluation Dataset for Intent "
        "Classification and Out-of-Scope Prediction",
        "authorship": "crowd workers wrote queries by paraphrasing seeds or answering scenarios",
    },
    "cookdial": {
        "url": "https://github.com/YiweiJiang2015/CookDial",
        "file": "CookDial",
        "licence": "MIT",
        "citation": "Jiang et al. (2022), CookDial: a dataset for task-oriented dialogs "
        "grounded in procedural documents",
        "authorship": "human user turns in human-to-human Wizard-of-Oz cooking dialogs",
    },
    "wizard_of_tasks": {
        "url": "https://wizard-of-tasks.s3.us-west-2.amazonaws.com/"
        "wizard_of_tasks_cooking_v1.0.json",
        "file": "wot_cooking.json",
        "licence": "CC BY-SA 4.0",
        "citation": "Choi et al. (2022), COLING, Wizard of Tasks",
        "authorship": "student turns written by Mechanical Turk workers",
    },
    "openwetware": {
        "url": "https://openwetware.org/mediawiki/api.php",
        "file": "oww_candidates.json",
        "licence": "CC BY-SA 3.0 (OpenWetWare content licence)",
        "citation": "OpenWetWare lab notebook pages (page id and revision recorded per item)",
        "authorship": "notebook text written by the researchers who own each page",
    },
}

WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15,
    "twenty": 20, "thirty": 30, "forty": 40, "forty-five": 45, "fifty": 50,
    "sixty": 60, "ninety": 90, "a": 1, "an": 1,
}  # fmt: skip
DURATION = re.compile(
    r"\b(\d+|" + "|".join(sorted(WORD_NUMBERS, key=len, reverse=True)) + r")[\s-]*"
    r"(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b",
    re.I,
)
FACTORS = {"s": 1, "m": 60, "h": 3600}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s']", " ", text.casefold()).split())


def file_sha(path: Path) -> str:
    if path.is_dir():
        digest = hashlib.sha256()
        for child in sorted(path.rglob("*.json")):
            digest.update(child.relative_to(path).as_posix().encode())
            digest.update(child.read_bytes())
        return digest.hexdigest()
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- Fetching -------------------------------------------------------------


def fetch(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for key in ("clinc150", "wizard_of_tasks"):
        target = directory / SOURCES[key]["file"]
        if not target.exists():
            urllib.request.urlretrieve(SOURCES[key]["url"], target)  # noqa: S310
    cookdial = directory / "CookDial"
    if not cookdial.exists():
        subprocess.run(
            ["git", "clone", "--depth", "1", SOURCES["cookdial"]["url"], str(cookdial)],
            check=True,
        )
    if not (directory / SOURCES["openwetware"]["file"]).exists():
        fetch_openwetware(directory / SOURCES["openwetware"]["file"])


OWW_QUERIES = (
    '"pH was measured"', '"the pH was"', '"measured the pH"', '"pH of the solution was"',
    '"the temperature was"', '"weighed"', '"final volume was"', '"total volume was"',
    '"was measured to be"', '"mass was"', '"we measured"', '"recorded the"',
    '"the pH is"', '"temperature is"', '"mL was"', '"final pH"',
)  # fmt: skip
_OWW_UNIT = (
    r"(?:°\s?C|degrees?\s+C(?:elsius)?|celsius|mL|ml|µL|uL|ul|microliters?|milliliters?"
    r"|mg|g|grams?|kg|rpm|pH|%|percent|L\b|liters?|litres?)"
)


def _oww(params: dict[str, Any]) -> Any:
    url = SOURCES["openwetware"]["url"] + "?" + urllib.parse.urlencode({**params, "format": "json"})
    request = urllib.request.Request(url, headers={"User-Agent": "LabJarvis-research/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return json.load(response)


def fetch_openwetware(target: Path) -> None:
    """Collect candidate sentences (number + unit, 3-18 words) from notebook pages."""

    candidates: list[dict[str, Any]] = []
    seen: set[int] = set()
    for query in OWW_QUERIES:
        hits = _oww({"action": "query", "list": "search", "srsearch": query, "srlimit": 40})
        for hit in hits["query"]["search"]:
            page_id = int(hit["pageid"])
            if page_id in seen:
                continue
            seen.add(page_id)
            try:
                page = _oww({"action": "parse", "pageid": page_id, "prop": "wikitext|revid"})
                parsed = page["parse"]
            except (KeyError, OSError, ValueError):
                continue
            text = parsed["wikitext"]["*"]
            text = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", text)
            text = re.sub(r"\[\[([^\]]*)\]\]", r"\1", text)
            text = re.sub(r"<[^>]+>|'''|''|\{\{[^}]*\}\}|^[*#:=|!]+", " ", text, flags=re.M)
            for raw in re.findall(r"[^.!?\n]*\d[^.!?\n]*", text):
                sentence = " ".join(raw.split())
                if 3 <= len(sentence.split()) <= 18 and re.search(
                    r"\d(?:\.\d+)?\s*" + _OWW_UNIT, sentence, re.I
                ):
                    candidates.append(
                        {
                            "pageid": page_id,
                            "title": parsed["title"],
                            "revid": parsed.get("revid"),
                            "sentence": sentence,
                        }
                    )
            time.sleep(0.3)
    target.write_text(json.dumps(candidates, indent=1, ensure_ascii=False), encoding="utf-8")


# --- Rules ----------------------------------------------------------------


def item(
    source: str,
    source_ref: str,
    text: str,
    intent: str,
    parameters: dict[str, str] | None = None,
    *,
    rule: str,
    acceptable: Iterable[str] = (),
    reason: str = "",
) -> dict[str, Any]:
    valid = intent != "UNSUPPORTED"
    return {
        "transcript": text,
        "expected_intent": intent,
        "expected_parameters": parameters or {},
        "acceptable_intents": sorted({intent, *acceptable}),
        "action_valid": valid,
        "session_state": "ACTIVE",
        "category": f"heldout_{intent.casefold()}" if valid else f"heldout_unsupported_{reason}",
        "source": source,
        "source_ref": source_ref,
        "licence": SOURCES[source]["licence"],
        "inclusion_rule": rule,
        "human_written": True,
        "synthetic": False,
        "split": "heldout",
    }


def duration_seconds(text: str) -> int | None:
    found = DURATION.findall(text)
    if len(found) != 1:
        return None
    number, unit = found[0]
    value = int(number) if number.isdigit() else WORD_NUMBERS[number.casefold()]
    return value * FACTORS[unit[0].casefold()]


def clinc_items(directory: Path, variant: str = "A") -> list[dict[str, Any]]:
    data = json.loads((directory / SOURCES["clinc150"]["file"]).read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    # Deviation D2 (pre-results): CLINC's validation split is used as well as its
    # test split. Neither is used anywhere in development, so both are held out;
    # the test split alone had too few timer durations for the numeric minimum.
    splits = ("test", "val") if variant == "A" else ("train",)
    clinc_rows = [
        (split, i, t, n) for split in splits for i, (t, n) in enumerate(data[split])
    ]
    for split, index, text, intent in clinc_rows:
        ref = f"{split}[{index}]:{intent}"
        lowered = text.casefold()
        if intent == "timer":
            asks_for_timer = re.search(r"timer|count\s*down", lowered) is not None
            if not asks_for_timer or re.search(r"\b(who|how do|what time|am|pm)\b", lowered):
                continue
            seconds = duration_seconds(text)
            if seconds is not None:
                rows.append(
                    item("clinc150", ref, text, "SET_TIMER", {"duration_seconds": str(seconds)},
                         rule="R1 timer with one duration")
                )  # fmt: skip
            elif not DURATION.search(text) and "timer" in lowered:
                rows.append(
                    item("clinc150", ref, text, "UNSUPPORTED", rule="R1 timer without duration",
                         reason="incomplete")
                )  # fmt: skip
        elif intent == "repeat":
            if re.search(r"\b(question|ask|asked)\b", lowered):
                continue
            rows.append(item("clinc150", ref, text, "REPEAT_INSTRUCTION", rule="R2 CLINC repeat"))
        elif intent == "measurement_conversion":
            rows.append(
                item("clinc150", ref, text, "UNSUPPORTED", rule="R9 unit-conversion distractor",
                     reason="conversion")
            )  # fmt: skip
        elif intent == "what_can_i_ask_you":
            if re.search(r"\blike\b", lowered):
                continue
            rows.append(item("clinc150", ref, text, "HELP", rule="R3 CLINC what_can_i_ask_you"))
    oos_split = "oos_test" if variant == "A" else "oos_train"
    oos = sorted((text for text, intent in data[oos_split]), key=sha)[:OOS_COUNT]
    for text in oos:
        rows.append(
            item("clinc150", oos_split, text, "UNSUPPORTED", rule="R7 CLINC oos",
                 reason="out_of_scope")
        )  # fmt: skip
    return rows


NEXT = re.compile(
    r"\b(what'?s next|what is next|whats next|next step|what next|what now|now what|"
    r"what should i do (now|next)|what do i do (now|next)|next\?)",
    re.I,
)
CONDITIONAL = re.compile(r"^\s*(once|after|when|while|if)\b", re.I)
COMPLETION = re.compile(r"\b(done|did it|finished|i have|i've|ive|is in|are in|added|placed)\b", re.I)
PURE_DONE = re.compile(
    r"^(ok[.,!]?\s*)?(i have done that|i've done that|i did it|done|that'?s done|done that|"
    r"ok done)[.!]*$",
    re.I,
)


def cookdial_items(directory: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((directory / "CookDial" / "data" / "dialog").glob("*.json")):
        dialog = json.loads(path.read_text(encoding="utf-8"))
        for message in dialog["messages"]:
            if message.get("bot"):
                continue
            annotations = json.loads(message["annotations"])
            intents = tuple(x.strip() for x in annotations["intent"].split(";") if x.strip())
            text = message["utterance"].strip()
            ref = f"{path.name}:{message['utt_id']}"
            section = annotations.get("section")
            words = len(text.split())
            if (
                "req_instruction" in intents
                and NEXT.search(text)
                and words <= 14
                and not CONDITIONAL.search(text)
                and "ingredient" not in text.casefold()
            ):
                acceptable = ("COMPLETE_STEP",) if COMPLETION.search(text) else ()
                rows.append(
                    item("cookdial", ref, text, "GET_NEXT_STEP", rule="R4 next-step request",
                         acceptable=acceptable)
                )  # fmt: skip
            elif intents == ("confirm",) and section == "inst" and PURE_DONE.match(text):
                rows.append(item("cookdial", ref, text, "COMPLETE_STEP", rule="R5 completion"))
            elif (
                intents == ("req_repeat",)
                and section == "inst"
                and "repeat" in text.casefold()
                and not re.search(r"ingredient|tool|word|temperature", text, re.I)
            ):
                rows.append(item("cookdial", ref, text, "REPEAT_INSTRUCTION", rule="R2 repeat"))
            elif "?" not in text:
                reading = REPORTED_VALUE.search(text)
                if reading is None:
                    continue
                kind = reading.group("kind").casefold()
                unit_text = " ".join(reading.group("unit").casefold().split())
                if kind in {"temperature", "oven", "fryer", "it"} and unit_text in {
                    "degrees celsius", "degree celsius", "°c", "celsius",
                }:
                    rows.append(
                        item("cookdial", ref, text, "RECORD_MEASUREMENT",
                             {"kind": "temperature", "value": reading.group("value"), "unit": "degC"},
                             rule="R6b reported value (CookDial)")
                    )  # fmt: skip
                elif unit_text in {"degrees", "degree"}:
                    rows.append(
                        item("cookdial", ref, text, "UNSUPPORTED",
                             rule="R6b reported value without a temperature scale",
                             reason="ambiguous")
                    )  # fmt: skip
    return rows


REPORTED_VALUE = re.compile(
    r"\b(?:the\s+(?:internal\s+)?)?(?P<kind>temperature|oven|fryer|it)\s+is\s+"
    r"(?:now\s+)?(?:at\s+|about\s+|heated\s+to\s+)?(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>degrees?\s+celsius|°c|celsius|degrees?)\b",
    re.I,
)


def wizard_items(directory: Path, variant: str = "A") -> list[dict[str, Any]]:
    data = json.loads((directory / SOURCES["wizard_of_tasks"]["file"]).read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for conversation_id, conversation in sorted(data.items()):
        wanted = {"test"} if variant == "A" else {"train", "validation"}
        if conversation.get("data_split") not in wanted:
            continue
        for turn in conversation["turns"]:
            text = (turn.get("text") or "").strip()
            if turn.get("role") != "student" or turn.get("intent") != "request_next_step":
                continue
            if (
                NEXT.search(text)
                and len(text.split()) <= 14
                and not CONDITIONAL.search(text)
                and "ingredient" not in text.casefold()
            ):
                acceptable = ("COMPLETE_STEP",) if COMPLETION.search(text) else ()
                rows.append(
                    item("wizard_of_tasks", f"{conversation_id}:{turn['turn_counter']}", text,
                         "GET_NEXT_STEP", rule="R4 next-step request", acceptable=acceptable)
                )  # fmt: skip
    return rows


QUANTITY = re.compile(
    r"^(?:the\s+)?(?:(?:final|measured|resulting)\s+)?(?P<kind>ph|temperature|volume|mass|weight)"
    r"(?:\s+of\s+(?:the\s+)?[a-z ]{1,30}?)?\s+(?:was|is|reading was|measured)\s+"
    r"(?:measured\s+(?:to\s+be|at|as)\s+|found\s+to\s+be\s+|approximately\s+|about\s+|~\s*)?"
    r"(?P<value>-?\d+(?:\.\d+)?)\s*(?P<unit>°\s?c|degrees?\s+c(?:elsius)?|celsius|ml|µl|ul|"
    r"microliters?|milliliters?|mg|g|grams?|kg|l|liters?|litres?)?\s*\.?$",
    re.I,
)
SETPOINT = re.compile(r"\b(set|adjust|adjusted|kept|maintain|maintained|should|must|add)\b", re.I)
UNIT_CANON = {
    "°c": "degC", "° c": "degC", "degrees c": "degC", "degree c": "degC",
    "degrees celsius": "degC", "degree celsius": "degC", "celsius": "degC",
    "ml": "mL", "milliliter": "mL", "milliliters": "mL", "µl": "uL", "ul": "uL",
    "microliter": "uL", "microliters": "uL", "mg": "mg", "g": "g", "gram": "g",
    "grams": "g", "kg": "kg", "l": "L", "liter": "L", "liters": "L", "litre": "L",
    "litres": "L",
}  # fmt: skip


def openwetware_items(directory: Path) -> list[dict[str, Any]]:
    path = directory / SOURCES["openwetware"]["file"]
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for candidate in json.loads(path.read_text(encoding="utf-8")):
        sentence = candidate["sentence"].strip()
        match = QUANTITY.match(sentence)
        if match is None or SETPOINT.search(sentence):
            continue
        kind = match.group("kind").casefold()
        kind = "mass" if kind == "weight" else kind
        raw_unit = (match.group("unit") or "").casefold()
        if kind == "ph":
            if raw_unit:
                continue
            unit = "pH"
        else:
            unit = UNIT_CANON.get(" ".join(raw_unit.split()), "")
            if not unit:
                continue
        rows.append(
            item(
                "openwetware",
                f"page {candidate['pageid']} rev {candidate['revid']} ({candidate['title']})",
                sentence,
                "RECORD_MEASUREMENT",
                {"kind": kind, "value": match.group("value"), "unit": unit},
                rule="R6 single reported measurement",
            )
        )
    return rows


def build(directory: Path, variant: str = "A", exclude: set[str] | None = None) -> list[dict[str, Any]]:
    candidates = (
        clinc_items(directory, variant)
        + cookdial_items(directory)
        + wizard_items(directory, variant)
        + openwetware_items(directory)
    )
    excluded = exclude or set()
    seen: set[str] = set()
    cells: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for candidate in sorted(candidates, key=lambda row: sha(row["transcript"])):
        key = norm(candidate["transcript"])
        if not key or key in seen or key in excluded:
            continue
        seen.add(key)
        cells[(candidate["source"], candidate["category"])].append(candidate)
    selected: list[dict[str, Any]] = []
    for (_, category), rows in sorted(cells.items()):
        # The out-of-scope cell is already limited to OOS_COUNT by rule R7.
        if category.endswith("out_of_scope"):
            cap = OOS_COUNT
        elif category == "heldout_set_timer":
            cap = TIMER_CAP
        elif category.endswith("conversion"):
            cap = CONVERSION_COUNT
        else:
            cap = CELL_CAP
        selected.extend(rows[:cap])
    selected.sort(key=lambda row: (row["expected_intent"], sha(row["transcript"])))
    for index, row in enumerate(selected, 1):
        row["id"] = f"human-{index:03d}"
    return selected


def coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    per_intent: dict[str, int] = defaultdict(int)
    for row in rows:
        per_intent[row["expected_intent"]] += 1
    supported = {k: v for k, v in per_intent.items() if k != "UNSUPPORTED"}
    numeric = sum(
        1 for row in rows if row["expected_intent"] in {"SET_TIMER", "RECORD_MEASUREMENT"}
    )
    minimums = {
        "total>=80": len(rows) >= 80,
        "supported_intents_with_5>=5": sum(v >= 5 for v in supported.values()) >= 5,
        "numeric_items>=20": numeric >= 20,
        "unsupported>=10": per_intent.get("UNSUPPORTED", 0) >= 10,
    }
    return {
        "total": len(rows),
        "per_intent": dict(sorted(per_intent.items())),
        "numeric_items": numeric,
        "minimums": minimums,
        "minimums_met": all(minimums.values()),
        "uncovered_supported_intents": sorted(
            {
                "GET_CURRENT_STEP", "GET_NEXT_STEP", "REPEAT_INSTRUCTION", "RECORD_MEASUREMENT",
                "RECORD_OBSERVATION", "SET_TIMER", "VERIFY_REAGENT", "CORRECT_PREVIOUS_ENTRY",
                "PAUSE_SESSION", "RESUME_SESSION", "COMPLETE_STEP", "END_SESSION", "HELP",
            }  # fmt: skip
            - set(supported)
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument(
        "--variant",
        choices=("A", "B"),
        default="A",
        help="A: the primary held-out set; B: an untouched confirmation set from "
        "source rows never used in A (CLINC train, Wizard-of-Tasks train/validation, "
        "unselected CookDial turns)",
    )
    args = parser.parse_args()
    if args.fetch:
        fetch(args.sources)
    exclude: set[str] = set()
    if args.variant == "B":
        exclude = {
            norm(json.loads(line)["transcript"])
            for line in OUTPUT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    rows = build(args.sources, args.variant, exclude)
    if args.variant == "B":
        for row in rows:
            row["id"] = row["id"].replace("human-", "humanB-")
            row["split"] = "heldout_b"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    provenance = {
        "manifest": args.output.resolve().as_posix().replace(ROOT.as_posix() + "/", ""),
        "manifest_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        "sources": {
            key: {
                **meta,
                "sha256": file_sha(args.sources / meta["file"])
                if (args.sources / meta["file"]).exists()
                else "missing",
            }
            for key, meta in SOURCES.items()
        },
        "coverage": coverage(rows),
        "rules": "docs/evaluation/model-selection-protocol.md (Held-out set)",
    }
    (args.output.parent / (PROVENANCE.stem + ("" if args.variant == "A" else "-B") + ".json")).write_text(json.dumps(provenance, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(provenance["coverage"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
