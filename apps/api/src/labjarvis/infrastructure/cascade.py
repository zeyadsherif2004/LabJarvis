"""Deterministic-first cascade over the rule grammar and a language model.

Two modes, both conforming to ``TextUnderstandingPort``:

``rules_first``
    The grammar runs first. An utterance it recognises is answered by the
    grammar and the model is never called. Only phrasings the grammar does not
    recognise reach the model, whose proposal is accepted only if it is
    schema-valid and its entities are grounded in the utterance.

``agreement``
    As above, but a grammar match is also checked by the model. When both
    produce a supported command and they differ materially, or the model says
    the utterance is unsupported, the cascade **abstains** and asks the operator
    to rephrase instead of guessing. A model *failure* (unreachable, invalid
    output) does not override the grammar; it is recorded.

Optionally a second, independent **verifier model** checks every LLM-routed
proposal that would change state (a measurement, observation, timer, pause,
resume, completion or end). The proposal survives only if both models produce
the same command after deterministic normalisation; otherwise the cascade
abstains. Read-only requests (current/next step, repeat, help, reagent check)
are harmless to answer from one model and are not double-checked. This is the
"model ensemble agreement" confidence source: agreement of two models is
observable evidence, a single model's self-report is not.

Every result carries its route, the grammar's and the model's readings, the
disagreement and the reason for any abstention, per-stage latency and the
model/prompt/schema identifiers. Model failures on the routed path are converted
into a non-mutating ``UNSUPPORTED`` proposal: the operator hears that the command
was not understood, and nothing is written.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from time import perf_counter
from typing import Literal

from labjarvis.domain.commands import validate_and_normalise_command
from labjarvis.domain.entity_grounding import DETERMINISTIC_MATCH, NO_EVIDENCE
from labjarvis.domain.models import ParsedCommand
from labjarvis.infrastructure.model_errors import (
    InferenceTimeoutError,
    ModelAdapterError,
    ModelOutputError,
)
from labjarvis.infrastructure.rule_grammar import GRAMMAR_VERSION, parse_rule_command
from labjarvis.ports.models import (
    AdapterReadiness,
    CommandContext,
    TextResult,
    TextUnderstandingPort,
)

CascadeMode = Literal["rules_first", "agreement"]
CASCADE_VERSION = "cascade-v2"
#: Intents whose acceptance changes the record or the session.
STATE_CHANGING_INTENTS = frozenset(
    {
        "RECORD_MEASUREMENT",
        "RECORD_OBSERVATION",
        "SET_TIMER",
        "PAUSE_SESSION",
        "RESUME_SESSION",
        "COMPLETE_STEP",
        "END_SESSION",
        "SKIP_STEP_WITH_REASON",
        "CORRECT_PREVIOUS_ENTRY",
        "PAUSE_TIMER",
        "RESUME_TIMER",
        "CANCEL_TIMER",
        "START_SESSION",
    }
)
VERIFIED_AGREEMENT = 0.9


def _elapsed_ms(started: float) -> str:
    return str(max(0, round((perf_counter() - started) * 1000)))


def _describe(command: ParsedCommand) -> str:
    return json.dumps(
        {"intent": command.intent, "parameters": dict(command.parameters)},
        sort_keys=True,
        separators=(",", ":"),
    )


def commands_agree(first: ParsedCommand, second: ParsedCommand) -> bool:
    """Material agreement after deterministic normalisation.

    Both readings are normalised by the production validator first, so "12.5 ml"
    and "12.5 mL", or "4" and "4.0", agree while 5 mL and 50 mL do not.
    """

    left = validate_and_normalise_command(first)
    right = validate_and_normalise_command(second)
    if left.intent != right.intent:
        return False
    left_parameters = dict(left.parameters)
    right_parameters = dict(right.parameters)
    if set(left_parameters) != set(right_parameters):
        return False
    for key, value in left_parameters.items():
        other = right_parameters[key]
        if key == "value":
            try:
                if Decimal(value) != Decimal(other):
                    return False
            except InvalidOperation:
                return False
        elif key == "observation":
            if " ".join(value.casefold().split()) != " ".join(other.casefold().split()):
                return False
        elif value.casefold() != other.casefold():
            return False
    return True


def _abstain(reason: str) -> ParsedCommand:
    return ParsedCommand(
        intent="UNSUPPORTED",
        parameters={},
        confidence=NO_EVIDENCE,
        ambiguities=(reason,),
    )


class CascadeTextUnderstanding:
    """A deterministic-first interpreter that consults a model only when useful."""

    adapter_name = "deterministic-first-cascade"

    def __init__(
        self,
        *,
        model: TextUnderstandingPort,
        mode: CascadeMode = "rules_first",
        wake_phrase: str = "Jarvis",
        verifier: TextUnderstandingPort | None = None,
    ) -> None:
        if mode not in ("rules_first", "agreement"):
            raise ValueError(f"unknown cascade mode: {mode}")
        self._model = model
        self._verifier = verifier
        self._mode: CascadeMode = mode
        self._wake_phrase = wake_phrase
        self.model_name = f"{GRAMMAR_VERSION}+{model.model_name}" + (
            f"+verified-by-{verifier.model_name}" if verifier is not None else ""
        )

    @property
    def mode(self) -> CascadeMode:
        return self._mode

    @property
    def language_model(self) -> TextUnderstandingPort:
        return self._model

    def _base_metadata(self) -> dict[str, str]:
        return {
            "interpreter": "cascade",
            "cascade_version": CASCADE_VERSION,
            "cascade_mode": self._mode,
            "rule_grammar": GRAMMAR_VERSION,
            "llm_adapter": self._model.adapter_name,
            "llm_model": self._model.model_name,
            "verifier_model": self._verifier.model_name if self._verifier else "",
        }

    def _consult_model(
        self,
        transcript: str,
        context: CommandContext,
    ) -> tuple[TextResult | None, str, str]:
        """Run the model; return (result, latency_ms, failure_code)."""

        started = perf_counter()
        try:
            result = self._model.interpret(transcript, context)
        except (ModelOutputError, InferenceTimeoutError) as exc:
            return None, _elapsed_ms(started), exc.code
        except ModelAdapterError as exc:
            return None, _elapsed_ms(started), exc.code
        return result, _elapsed_ms(started), ""

    @staticmethod
    def _model_metadata(result: TextResult | None) -> dict[str, str]:
        if result is None:
            return {}
        # Namespaced so the model adapter's provenance cannot overwrite the
        # cascade's own route fields.
        return {f"llm_{key}": value for key, value in result.metadata.items()}

    def interpret(self, transcript: str, context: CommandContext) -> TextResult:
        metadata = self._base_metadata()
        started = perf_counter()
        rule = parse_rule_command(transcript, wake_phrase=self._wake_phrase)
        metadata["rule_latency_ms"] = _elapsed_ms(started)
        metadata["rule_matched"] = "true" if rule.matched else "false"
        metadata["rule_name"] = rule.rule
        metadata["rule_result"] = _describe(rule.command)

        if rule.matched and self._mode == "rules_first":
            metadata.update(
                cascade_route="rule",
                llm_called="false",
                confidence_basis="deterministic grammar match",
            )
            return self._result(rule.command, metadata)

        model_result, latency_ms, failure = self._consult_model(transcript, context)
        metadata["llm_called"] = "true"
        metadata["llm_latency_ms"] = latency_ms
        metadata.update(self._model_metadata(model_result))
        if model_result is not None:
            metadata["llm_result"] = _describe(model_result.command)
        if failure:
            metadata["llm_failure"] = failure

        if rule.matched:
            # Agreement mode: the model checks the grammar.
            return self._agreement(rule.command, model_result, metadata)

        if model_result is None:
            metadata.update(
                cascade_route="llm_failed",
                abstention_reason=f"text model failed: {failure}",
                confidence_basis="no evidence: model failed",
            )
            return self._result(
                _abstain("the text model could not interpret this; try a supported phrasing"),
                metadata,
            )
        command = model_result.command
        if command.intent == "UNSUPPORTED":
            metadata.update(
                cascade_route="llm_unsupported",
                abstention_reason="; ".join(command.ambiguities) or "model abstained",
                confidence_basis=model_result.metadata.get("confidence_basis", "model abstained"),
            )
            return self._result(command, metadata)
        if self._verifier is not None and command.intent in STATE_CHANGING_INTENTS:
            return self._verify(command, transcript, context, metadata)
        metadata.update(
            cascade_route="llm",
            confidence_basis=model_result.metadata.get(
                "confidence_basis", "schema-valid grounded model proposal"
            ),
        )
        return self._result(command, metadata)

    def _verify(
        self,
        command: ParsedCommand,
        transcript: str,
        context: CommandContext,
        metadata: dict[str, str],
    ) -> TextResult:
        """Require an independent second model to reach the same command."""

        assert self._verifier is not None
        started = perf_counter()
        try:
            verdict = self._verifier.interpret(transcript, context)
        except ModelAdapterError as exc:
            metadata.update(
                verifier_latency_ms=_elapsed_ms(started),
                verifier_failure=exc.code,
                cascade_route="abstain_unverified",
                abstention_reason="the verifier model could not check this proposal",
                confidence_basis="no evidence: verifier failed",
            )
            return self._result(
                _abstain("the proposal could not be double-checked; please rephrase"), metadata
            )
        metadata["verifier_latency_ms"] = _elapsed_ms(started)
        metadata["verifier_result"] = _describe(verdict.command)
        if commands_agree(command, verdict.command):
            metadata.update(
                cascade_route="llm_verified",
                agreement="agree",
                confidence_basis="two independent models agree",
            )
            return self._result(
                ParsedCommand(
                    intent=command.intent,
                    parameters=command.parameters,
                    confidence=VERIFIED_AGREEMENT,
                ),
                metadata,
            )
        metadata.update(
            cascade_route="abstain_model_disagreement",
            agreement="disagree",
            disagreement="true",
            abstention_reason="the two text models interpreted this differently",
            confidence_basis="no evidence: models disagree",
        )
        return self._result(
            _abstain("the interpretation is uncertain; please rephrase the command"), metadata
        )

    def _agreement(
        self,
        rule_command: ParsedCommand,
        model_result: TextResult | None,
        metadata: dict[str, str],
    ) -> TextResult:
        if model_result is None:
            metadata.update(
                cascade_route="rule_unverified",
                agreement="unverified",
                confidence_basis="deterministic grammar match; model check unavailable",
            )
            return self._result(rule_command, metadata)
        model_command = model_result.command
        if rule_command.intent == "UNSUPPORTED":
            # A recognised compound command: refused whatever the model says.
            metadata.update(
                cascade_route="rule",
                agreement=(
                    "agree" if model_command.intent == "UNSUPPORTED" else "model_overruled"
                ),
                confidence_basis="deterministic refusal",
            )
            return self._result(rule_command, metadata)
        if commands_agree(rule_command, model_command):
            metadata.update(
                cascade_route="agree",
                agreement="agree",
                confidence_basis="grammar and model agree",
            )
            agreed = ParsedCommand(
                intent=rule_command.intent,
                parameters=rule_command.parameters,
                confidence=DETERMINISTIC_MATCH,
                ambiguities=rule_command.ambiguities,
            )
            return self._result(agreed, metadata)
        metadata.update(
            cascade_route="abstain_disagreement",
            agreement="disagree",
            disagreement="true",
            abstention_reason="the grammar and the text model interpreted this differently",
            confidence_basis="no evidence: interpreters disagree",
        )
        return self._result(
            _abstain("interpreters disagree; please rephrase the command"),
            metadata,
        )

    def _result(self, command: ParsedCommand, metadata: Mapping[str, str]) -> TextResult:
        return TextResult(
            command=command,
            adapter=self.adapter_name,
            model_name=self.model_name,
            metadata=dict(metadata),
        )

    def readiness(self) -> AdapterReadiness:
        """The grammar is always ready; the model's readiness is reported as-is."""

        model = self._model.readiness()
        detail = (
            f"grammar ready; text model: {model.detail}"
            if model.ready
            else f"grammar ready (supported phrasings work); text model not ready: {model.detail}"
        )
        return AdapterReadiness(
            ready=model.ready,
            adapter=self.adapter_name,
            model_name=self.model_name,
            detail=detail,
            configured=model.configured,
            dependency_installed=model.dependency_installed,
            model_cached=model.model_cached,
            loaded=model.loaded,
            provider_reachable=model.provider_reachable,
            inference_tested=model.inference_tested,
        )

    def close(self) -> None:
        for model in (self._model, self._verifier):
            close = getattr(model, "close", None)
            if callable(close):
                close()
