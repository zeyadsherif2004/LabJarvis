# ADR 0018: A hands-free and eyes-free bench workflow

- **Status:** Accepted (2026-09-27). Extends ADR 0014 and ADR 0017.
- **Code:** `domain/voice_controls.py`, `domain/references.py`,
  `domain/voice_confirmation.py`, `infrastructure/hands_free_grammar.py`
  (grammar v2), `infrastructure/hands_free.py`, `application/service.py`
  (`_resolve_references`, `_handle_voice_control`, `_proposal_text`,
  `_committed_text`, `_apply_correction`), `media/speechOutput.ts`,
  `features/session/{HandsFreeListeningPanel,HandsFreeStartPrompt,timerAlarms}.ts(x)`,
  `scripts/tasks.py hands-free`.

## Definitions

- **Hands-free:** no manual input to the computer during the bench workflow.
  Holding a bottle up to the camera is part of the lab task, not operating the
  computer.
- **Eyes-free:** every outcome the researcher needs is spoken; the screen shows
  the same information but is never the only channel.
- **Bench workflow:** everything between opening the session and ending it.
  Protocol import, review and approval stay screen-based on purpose: review
  happens before gloving up.
- **Touch budget:** at most one touch per session, at session start (browsers
  require a user gesture before audio playback and capture). In the documented
  kiosk launch mode that removes the gesture requirement, the target is zero.

## Context

ADR 0017 made the core loop (measure, confirm, check a label) possible by voice
but a hands-free audit found eleven gaps: listening needed a button and timed
out after 10 minutes; finished timers were silent; protocol questions,
timer control, skipping, correcting, reagent choice, starting or resuming the
session, and silencing an alarm all needed the screen; hands-free operation
took five settings; and several results were only visible.

## Decision

1. **Always-on listening for the session.** `LABJARVIS_HANDS_FREE_AUTOSTART`
   starts listening when a session screen opens, after one "Tap once to start
   hands-free" gesture that also asks for microphone and camera permission.
   While a session is open (READY, ACTIVE or PAUSED) there is no idle timeout.
   A microphone or detector failure is announced and retried three times with
   backoff (1, 2, 4 s) before a tap is requested.
2. **Interface controls by voice**, matched by exact phrase before any
   interpretation and never touching the record: "stop listening" (standby -
   only "start listening" is then accepted, and only while the tab has focus),
   "start listening", "mute", "unmute", "say that again", "stop"/"quiet"
   (barge-in) and "stop the alarm"/"timer done".
3. **Barge-in without weakening the echo guard.** A segment that starts during,
   or within 1 s of, LabJarvis speaking or sounding an alarm is still never a
   command or an answer. It is sent in `barge_in` mode, where the server honours
   only stop/quiet, mute and stop-the-alarm and discards everything else
   without transcript, retention or record. Spoken prompts never contain the
   wake phrase, so an echo cannot even address LabJarvis.
4. **Timer alarms from server state.** An ELAPSED timer whose id is not in any
   `timer_alarm_acknowledged` audit event is `alarm_active`; the browser speaks
   "Timer ... is done" with a tone every 20 s until acknowledged. Because the
   state is derived on every read, an alarm survives a reload. Warnings at 60 s
   and 10 s (configurable) unless muted; the alarm is shown as text with an
   icon, not colour alone.
5. **New voice intents**, each following the command-contract pattern
   (contract v3 branch, grammar v2 phrasing, grounding, tool registry,
   read-back phrase, prompt, tests): PAUSE/RESUME/CANCEL_TIMER (timer resolved
   by duration, step or label; ambiguity is asked about), GET_ACTIVE_TIMERS,
   ANSWER_PROTOCOL_QUESTION (retrieval v1 answers; the model only routes),
   GET_EXPECTED_REAGENTS, GET_PROTOCOL_STATE, READ_LAST_ENTRY,
   SKIP_STEP_WITH_REASON (reason required), CORRECT_PREVIOUS_ENTRY (the most
   recent or a named measurement; chained, original preserved, fails closed if
   overtaken), VERIFY_REAGENT with a name, alias or number (never a silent
   default), START_SESSION and RESUME_SESSION.
6. **Voice-only read-back phrases** restate the content: "confirm skip step N",
   "confirm 12 millilitres" (new value of a correction), "confirm cancel 10
   minute timer", "confirm pause timer", "confirm start session", "confirm
   resume session". A reply naming a different action never confirms, and a
   restated "cancel" is not read as a refusal only when a timer cancellation
   is waiting and the reply affirms and names the timer.
7. **Observations voice-only (opt-in, weaker).**
   `LABJARVIS_VOICE_OBSERVATIONS_ENABLED`: LabJarvis reads the text back ("I
   heard: ..."), and "confirm observation" commits only when both
   transcriptions of the dictation agreed. This is weaker than value read-back
   because free text cannot be restated reliably: the operator confirms that
   they heard the read-back, not that they repeated it.
8. **One profile.** `LABJARVIS_HANDS_FREE_PROFILE=true` switches on listening,
   autostart, voice-only confirmation, the cross-check (default `base.en`, the
   only place a default model name is allowed), warm-up and alarms. Vision on
   this computer offers live scanning (default) or a browser-saved timed
   single-frame mode; hosted Vision captures one frame after a spoken countdown
   on an explicit reagent-check request. Startup is refused
   with a missing required local model named. Readiness and Diagnostics
   show one "Hands-free: ready" line or exactly what is missing.
9. **Eyes-free output.** Every outcome is spoken through one module: proposals
   with the phrase to say, commits ("Recorded 10 millilitres for volume in step
   1."), refusals with the reason, rejections, errors in plain language, scan
   prompts, label results, timer events and answers. `LABJARVIS_SPOKEN_DETAIL`
   = brief keeps the outcome and the phrase to say.
10. **Kiosk launch.** `python scripts/tasks.py hands-free` starts the profile and
    opens Chrome (or Edge) in app mode with `--autoplay-policy=no-user-gesture-required`
    on `http://127.0.0.1:5173/?kiosk=1`, a dedicated profile, localhost only.
    The page checks that audio actually runs without a gesture, shows a
    kiosk banner, and otherwise falls back to the one-tap prompt.

## Grammar compatibility

Grammar v2 runs every version-1 rule first; the hands-free rules are consulted
only when v1 matched nothing (`parse_rule_command(..., hands_free=False)`
reproduces v1). On the frozen 81-command benchmark and both held-out paraphrase
sets, no v1 result changes (unit-tested). The new rules newly match 2/81, 31/138
and 29/139 utterances; none of these is a state change the label did not ask
for - most are off-topic wh-questions now routed to protocol Q&A, which
abstains ("I couldn't find that in the approved protocol") instead of the
labelled UNSUPPORTED. Frozen benchmark numbers were produced with v1 and are
not restated.

## Consequences

- Automated evidence (fake models): a Playwright journey runs a full protocol
  with one tap and 35 wake-phrase utterances and no other interaction; with the
  kiosk flag, zero. Synthetic speech through real local models is reported in
  `research/final_report_evidence/hands-free/`. One developer-operated
  [real-voice session](../../../research/final_report_evidence/real-world/session-2026-09-28-saline.md)
  followed; its build changed during the run, and false-wake behaviour was
  not measured.
- Listening for a whole session is a larger privacy surface: it is opt-in,
  local, visible, announced, stoppable by voice, and unaddressed speech leaves
  no trace (threat model T-HF).
- Still screen-based by design: import, review, approval, typed correction of
  arbitrary fields, and correcting anything other than a measurement.
