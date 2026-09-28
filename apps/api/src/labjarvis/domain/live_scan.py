"""The stability rule for live ("scan until stable") label checking.

In live mode the camera is sampled every ``interval_ms``; each frame is read by
the local OCR and compared with the expected reagent (a *probe*: nothing is
recorded). A MATCH is stable once ``required_consecutive`` readable probes
among the most recent ``required_consecutive + TOLERATED_OUTLIERS`` agree on the
comparison outcome. A MISMATCH or UNCERTAIN also needs those probes to agree on
the exact normalised text. It times out after ``timeout_ms`` or ``max_frames``
probes, whichever comes first.

Rule ``stable-asymmetric-v3`` replaced ``stable-agreement-v2``, which could
settle on three different MISMATCH texts produced while a correct label was
moving into view. Earlier rule ``stable-consecutive-v1`` needed
the last three probes to have character-identical text. Measured on the app's
own OCR, a hand-held handwritten label is read exactly in roughly three frames
out of four; the rest are small misreadings ("sodiuw chloride"), and an
unreadable frame appears whenever the label tilts. Under v1 any one of those
restarted the count, so a label held as still as a person can hold it rarely
produced three identical frames in a row and the scan timed out asking the
operator to "hold the label steady". v2 tolerates up to two stray probes in the
window and asks the probes to agree on what the comparison concluded, which is
also what the multi-frame verification afterwards requires (``unanimous-v1``
agrees on outcomes, not on spelling).

Stability still authorises nothing. A stable scan only selects which frames are
sent to the ordinary multi-frame verification: exactly the agreeing frames, so
each of them reached the same outcome on its own. That verification creates a
*pending* reading; recording it still needs an explicit human confirmation, and
a MISMATCH or UNCERTAIN reading can never be confirmed as verified. A MATCH can
only come from frames that each read the expected name (or an alias) exactly.

The same rule is implemented in ``apps/web/src/media/liveScan.ts`` for the
browser; both are checked against ``apps/api/tests/fixtures/live_scan_vectors.json``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

RULE_VERSION = "stable-asymmetric-v3"
UNREADABLE = "UNREADABLE"
#: Probes in the window that may disagree (or be unreadable) without resetting.
TOLERATED_OUTLIERS = 2


@dataclass(frozen=True, slots=True)
class ScanRule:
    required_consecutive: int = 3
    interval_ms: int = 400
    timeout_ms: int = 12_000

    def __post_init__(self) -> None:
        if not 2 <= self.required_consecutive <= 10:
            raise ValueError("required_consecutive must be between 2 and 10")
        if not 100 <= self.interval_ms <= 5_000:
            raise ValueError("interval_ms must be between 100 and 5000")
        if not 1_000 <= self.timeout_ms <= 120_000:
            raise ValueError("timeout_ms must be between 1000 and 120000")

    @property
    def max_frames(self) -> int:
        return max(self.required_consecutive, -(-self.timeout_ms // self.interval_ms))

    @property
    def window(self) -> int:
        return self.required_consecutive + TOLERATED_OUTLIERS


@dataclass(frozen=True, slots=True)
class ProbeReading:
    """One probe: the comparison outcome and the normalised visible text."""

    outcome: str
    normalised: str

    @property
    def readable(self) -> bool:
        return self.outcome != UNREADABLE and bool(self.normalised.strip())


class ScanStatus(StrEnum):
    SCANNING = "scanning"
    STABLE = "stable"
    TIMEOUT = "timeout"


@dataclass(frozen=True, slots=True)
class ScanState:
    status: ScanStatus
    frames_examined: int
    #: For STABLE: the agreed outcome and the commonest agreeing text.
    outcome: str = ""
    normalised: str = ""
    #: For STABLE: indexes (into the readings) of the agreeing probes to submit.
    frame_indexes: tuple[int, ...] = ()
    #: For TIMEOUT: "unreadable" or "unsteady". Empty otherwise.
    reason: str = ""
    #: A short spoken/visible prompt for the operator, or empty.
    prompt: str = ""


def _agreement(
    readings: Sequence[ProbeReading], rule: ScanRule
) -> tuple[str, tuple[int, ...]] | None:
    start = max(0, len(readings) - rule.window)
    by_reading: dict[tuple[str, str], list[int]] = {}
    for index in range(start, len(readings)):
        reading = readings[index]
        if reading.readable:
            key = (reading.outcome, "" if reading.outcome == "MATCH" else reading.normalised)
            by_reading.setdefault(key, []).append(index)
    for (outcome, _normalised), indexes in by_reading.items():
        if len(indexes) >= rule.required_consecutive:
            # With a window of required + 2, at most one outcome can qualify.
            return outcome, tuple(indexes[-rule.required_consecutive :])
    return None


def evaluate_scan(
    readings: Sequence[ProbeReading], *, elapsed_ms: int, rule: ScanRule
) -> ScanState:
    """Decide the scan state after the latest probe."""

    count = len(readings)
    agreed = _agreement(readings, rule)
    if agreed is not None:
        outcome, indexes = agreed
        texts = Counter(readings[index].normalised for index in indexes)
        top = max(texts.values())
        # Commonest agreeing text; ties go to the most recent reading.
        normalised = next(
            readings[index].normalised
            for index in reversed(indexes)
            if texts[readings[index].normalised] == top
        )
        return ScanState(
            ScanStatus.STABLE,
            count,
            outcome=outcome,
            normalised=normalised,
            frame_indexes=indexes,
        )
    if count >= rule.max_frames or elapsed_ms >= rule.timeout_ms:
        unreadable = sum(not reading.readable for reading in readings)
        reason = "unreadable" if unreadable * 2 >= max(count, 1) else "unsteady"
        prompt = (
            "I could not read the label. Move it closer, avoid glare, and check again."
            if reason == "unreadable"
            else "The label reading kept changing. Hold it still and check again."
        )
        return ScanState(ScanStatus.TIMEOUT, count, reason=reason, prompt=prompt)
    recent = list(readings[-3:])
    prompt = ""
    if len(recent) == 3 and not any(reading.readable for reading in recent):
        prompt = "Move the label closer to the camera."
    elif len(recent) == 3 and len({r.outcome for r in recent if r.readable}) > 1:
        prompt = "Hold the label steady."
    return ScanState(ScanStatus.SCANNING, count, prompt=prompt)
