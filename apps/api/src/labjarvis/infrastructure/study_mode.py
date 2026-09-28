"""Study-only injection of plausible, pre-declared mistakes (seeded-error trials).

The formative study measures whether people catch *wrong but valid* proposals
under two confirmation designs. That needs proposals that are wrong on
purpose, on trials fixed in advance. This module wraps the ordinary adapters
and alters exactly the trials listed in a study script:

* a measurement value (for example 5 mL -> 50 mL, 37 degC -> 27 degC);
* the visible label text read by the vision stage (a wrong reagent).

Safeguards:

* it only exists when the operator sets **both** a study script and the exact
  acknowledgement phrase, outside production (validated in ``Settings``);
* every altered result carries ``study_injected=true`` plus the original value
  in its provenance, so analysis can never mistake an injection for a genuine
  model output;
* the readiness endpoint announces study mode, and the UI shows a permanent
  banner telling participants that some proposals are deliberately wrong.

Nothing here bypasses validation or confirmation: an injected proposal is still
validated, still needs a human decision, and is recorded as such.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from labjarvis.domain.models import ParsedCommand
from labjarvis.ports.models import (
    AdapterReadiness,
    CommandContext,
    LabelRecognitionPort,
    LabelResult,
    SpeechToTextPort,
    TextResult,
    TextUnderstandingPort,
)

STUDY_ACKNOWLEDGEMENT = "SEEDED-ERRORS-FOR-A-CONSENTED-STUDY"


class Injection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trial: int = Field(ge=1, le=100)
    kind: Literal["measurement_value", "reagent_label"]
    #: Replacement measurement value, or replacement visible label text.
    to: str = Field(min_length=1, max_length=120)
    note: str = Field(default="", max_length=200)


class StudyScript(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    study_id: str = Field(min_length=1, max_length=60)
    participant: str = Field(min_length=1, max_length=20, pattern=r"^P\d{2}$")
    condition: Literal["tap", "readback"]
    block: int = Field(ge=1, le=4)
    injections: tuple[Injection, ...] = Field(max_length=10)


def load_study_script(path: Path) -> StudyScript:
    return StudyScript.model_validate(json.loads(path.read_text(encoding="utf-8")))


@dataclass
class _Counters:
    measurement_trials: int = 0
    label_trials: int = 0


class StudyInjector:
    """Shared, thread-safe trial counters for one study session."""

    def __init__(self, script: StudyScript) -> None:
        self.script = script
        self._counters = _Counters()
        self._lock = threading.Lock()

    def next_measurement_trial(self) -> int:
        with self._lock:
            self._counters.measurement_trials += 1
            return self._counters.measurement_trials

    def next_label_trial(self) -> int:
        with self._lock:
            self._counters.label_trials += 1
            return self._counters.label_trials

    def injection_for(self, kind: str, trial: int) -> Injection | None:
        return next(
            (i for i in self.script.injections if i.kind == kind and i.trial == trial), None
        )

    def provenance(self, trial: int) -> dict[str, str]:
        return {
            "study_id": self.script.study_id,
            "study_participant": self.script.participant,
            "study_condition": self.script.condition,
            "study_block": str(self.script.block),
            "study_trial": str(trial),
        }


class StudyTextUnderstanding:
    """Counts measurement proposals as trials and alters the scripted ones."""

    def __init__(self, inner: TextUnderstandingPort, injector: StudyInjector) -> None:
        self._inner = inner
        self._injector = injector
        self.adapter_name = inner.adapter_name
        self.model_name = inner.model_name

    def interpret(self, transcript: str, context: CommandContext) -> TextResult:
        result = self._inner.interpret(transcript, context)
        if result.command.intent != "RECORD_MEASUREMENT":
            return result
        trial = self._injector.next_measurement_trial()
        metadata = {**dict(result.metadata), **self._injector.provenance(trial)}
        injection = self._injector.injection_for("measurement_value", trial)
        if injection is None:
            metadata["study_injected"] = "false"
            return replace(result, metadata=metadata)
        original = dict(result.command.parameters)
        altered = ParsedCommand(
            intent=result.command.intent,
            parameters={**original, "value": injection.to},
            confidence=result.command.confidence,
            ambiguities=result.command.ambiguities,
        )
        metadata.update(
            study_injected="true",
            study_original_value=original.get("value", ""),
            study_injected_value=injection.to,
        )
        return replace(result, command=altered, metadata=metadata)

    def readiness(self) -> AdapterReadiness:
        return self._inner.readiness()

    def close(self) -> None:
        close = getattr(self._inner, "close", None)
        if callable(close):
            close()


class StudyLabelRecognition:
    """Counts label readings as trials and replaces the scripted ones."""

    def __init__(self, inner: LabelRecognitionPort, injector: StudyInjector) -> None:
        self._inner = inner
        self._injector = injector
        self.adapter_name = inner.adapter_name
        self.model_name = inner.model_name

    def recognise(self, image: bytes, *, content_type: str) -> LabelResult:
        result = self._inner.recognise(image, content_type=content_type)
        trial = self._injector.next_label_trial()
        metadata = {**dict(result.metadata), **self._injector.provenance(trial)}
        injection = self._injector.injection_for("reagent_label", trial)
        if injection is None:
            metadata["study_injected"] = "false"
            return replace(result, metadata=metadata)
        metadata.update(
            study_injected="true",
            study_original_text=result.visible_text,
            study_injected_text=injection.to,
        )
        return replace(result, visible_text=injection.to, metadata=metadata)

    def readiness(self) -> AdapterReadiness:
        return self._inner.readiness()

    def close(self) -> None:
        close = getattr(self._inner, "close", None)
        if callable(close):
            close()


def unchanged_speech(speech: SpeechToTextPort) -> SpeechToTextPort:
    """Speech is never altered in study mode: participants' words are their own."""

    return speech
