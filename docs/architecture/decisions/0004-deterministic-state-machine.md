# ADR 0004: Deterministic state machine instead of autonomous agent

**Status:** Accepted  
**Date:** 2026-07-16

## Context

An LLM deciding whether a protocol step is complete or directly invoking tools would be difficult to reproduce, authorize, and audit. Stale or ambiguous commands must not mutate a session.

## Options

1. Tool-using autonomous agent.
2. Prompt-only conversational guidance.
3. AI proposals interpreted by deterministic state transitions with explicit confirmation.

## Decision

Use finite session/step transition maps, allowlisted commands, optimistic versions, pending actions, and explicit human decisions. Read-only guidance returns exact reviewed protocol text.

## Consequences

- Legal behavior is explainable and property/unit/integration tested.
- The assistant deliberately rejects broad scientific questions and multi-action ambiguity.
- Adding a command requires domain semantics, validation, persistence, UI confirmation, tests, and evaluation labels—not only a prompt edit.
- The system assists but cannot autonomously optimize a protocol.

