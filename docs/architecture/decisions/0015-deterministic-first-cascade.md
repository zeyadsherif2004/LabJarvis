# ADR 0015: Deterministic-first cascade and an intent-discriminated command contract

- **Status:** Accepted (2026-09-26). Replaces the earlier flat schema and
  model-reported confidence with an intent-discriminated contract.
- **Code:** `infrastructure/command_contract.py`, `infrastructure/interpreter_spec.py`, `infrastructure/rule_grammar.py`, `infrastructure/cascade.py`, `domain/entity_grounding.py`, `infrastructure/text_model_profiles.py`.

## Context

The first contract was one flat JSON object with every possible slot optional
and a model-reported `confidence`. Small local models filled slots that did not
belong to the chosen intent, invented values, and reported high confidence for
wrong proposals; on the authored benchmark `qwen2.5:1.5b` produced an exact
command for only 9.9% of valid items. The rule grammar, by contrast, never
produced an unsafe execution but understood almost no human-written paraphrase.

## Decision

1. **Intent-discriminated schema (`command-contract-v2`).** One `oneOf`/`anyOf`
   branch per intent, each allowing exactly its own fields; units are an enum;
   timers are whole seconds. The same schema drives Ollama grammar-constrained
   decoding and hosted strict structured output, and is re-validated in code.
2. **No model confidence.** Confidence is derived deterministically: a grammar
   match, a model proposal whose numbers, units, durations and observation
   wording all appear in the utterance (entity grounding), two-model agreement,
   or nothing (abstain).
3. **Deterministic first.** `TextInterpreter` is `rules`, `llm`, `cascade`
   (default outside fake mode: grammar first, model only for phrasings the
   grammar does not know) or `agreement` (the model must also agree with every
   grammar match). An optional verifier model (any provider) must agree with
   every LLM-routed state-changing proposal or the cascade abstains.
4. **Versioned specification.** The task instructions and 12 development-split
   few-shot examples are hashed (`interpreter-prompt-v2`) and recorded with every
   inference, with per-model profiles (e.g. thinking off for reasoning models).

## Consequences

- The contract, not model size, was the decisive change (1.5B: 9.9% -> 85.4%
  exact on the benchmark), and the grammar-first route keeps the grammar's zero
  unsafe executions for phrasings it knows.
- Remaining errors are wrong-but-valid proposals that no schema can catch; human
  confirmation stays the last barrier (see the model-selection record).
- Every model provider must honour the same schema; providers without strict
  schema support fall back to JSON mode or prompt-only and simply fail closed
  more often.
