# ADR 0017: Hands-free listening, voice-only confirmation and live label scanning

- **Status:** Accepted (2026-09-26/27). Extends ADR 0011, ADR 0012 and ADR 0014.
  Extended by ADR 0018 (2026-09-27), which closes the audit gaps listed under
  Consequences: listening stays on for a whole session, and timers, questions,
  skips, corrections, reagent choice and session start work by voice.
- **Code:** `apps/web/src/media/pushToTalk.ts`, `media/handsFreeListener.ts`, `features/session/HandsFreeListeningPanel.tsx`, `domain/wake_phrase.py`, `domain/voice_confirmation.py`, `domain/live_scan.py`, `media/liveScan.ts`, `application/service.py` (`handle_audio_command`, `confirm_pending_action_by_voice`, `confirm_reagent_verification_by_voice`, `probe_reagent_label`).

## Context

The earlier "hands-free" mode was tap-to-start/tap-to-stop capture with a hard
15-second bound, so every command still needed a touch. Spoken confirmation
(ADR 0014) removed the tap for confirming, but capture itself still did not.

## Decision

1. **Push-to-talk anywhere.** A configurable key (F8 by default; F9, F10, F12,
   Pause, ScrollLock, PageDown) works anywhere on the session page, so a USB foot
   pedal that emulates the key gives hands-free capture. Maximum 30 seconds;
   Escape cancels.
2. **Optional wake-phrase listening** (`LABJARVIS_HANDS_FREE_LISTENING_ENABLED`).
   Silero VAD runs in the browser; each detected segment is sent with
   `require_wake_phrase`, and the server discards anything not starting with the
   wake phrase before inference, retention or any record. The listener pauses
   while LabJarvis speaks or is busy and switches off after 10 minutes without
   speech.
3. **Voice-only confirmation** (`LABJARVIS_VOICE_ONLY_CONFIRMATION_ENABLED`,
   requires listening and the high-integrity transcriber). A wake-phrase segment
   may answer a waiting proposal or label reading only if it starts with the
   wake phrase and restates exactly one matching value: value and unit, timer
   duration, step number, the session action, or the reagent name (MATCH only).
   Observations and skipped steps cannot be confirmed voice-only. Spoken prompts
   never contain the wake phrase, prompt-like wording is refused, and the browser
   drops segments that start during or within 1 s of LabJarvis speaking.
4. **Live label scanning** (`LABJARVIS_LIVE_LABEL_SCAN_ENABLED`, local OCR only).
   Frames are probed without being recorded until `stable-asymmetric-v3` holds:
   N readable probes among the most recent N+2 agree on MATCH, or on both the
   outcome and the normalised text for MISMATCH/UNCERTAIN.
   Only the agreeing frames form an ordinary pending reading; the operator must
   still confirm it. This replaced `stable-consecutive-v1` after a handheld
   label kept timing out on small OCR variations and brief unreadable frames.
   An intermediate `stable-agreement-v2` accepted three different MISMATCH
   texts as stable and ended some correct-label scans too early, so v3 requires
   text agreement for those non-matching outcomes.
   A timed single-frame countdown is also available as a browser-saved choice
   for Local Vision. The voice command uses the selected mode; live scan stays
   the default. The three-frame countdown remains a fallback when live scanning
   is not configured. A single frame offers no cross-frame agreement and still
   needs explicit operator review.
   With hosted Vision, the hands-free profile leaves live scanning off. A named
   voice request speaks a countdown, captures one cropped frame and sends it to
   the hosted adapter for a single pending reading. There are no repeated
   remote probes; the observed text is read back where safe, and the operator
   still confirms or rejects the result explicitly.
5. A confirmed MISMATCH/UNCERTAIN reading is recorded as `not_matching`, never as
   a completed label review.

## Consequences

- The core bench loop can be driven by pedal or by voice alone; setup, protocol
  review, typed questions, timer controls, skips and corrections still use the
  screen (see the hands-free audit in the project notes).
- The design was first checked with 58 synthetic clips through real Whisper
  (0 false commits). A later developer-operated real-voice session is
  [reported separately](../../../research/final_report_evidence/real-world/session-2026-09-28-saline.md);
  participant and false-wake results are still unavailable.
- Always-on listening is a privacy change; it is opt-in, local, visible and
  announced, and unaddressed speech leaves no trace.
