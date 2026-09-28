# 0014. A spoken confirmation is a fresh microphone recording taken after the readback, not a function call

**Status:** Accepted (18 August 2026)
**Extends:** [0004](0004-deterministic-state-machine.md), [0011](0011-deterministic-core-realtime-shell.md), [0012](0012-selective-high-integrity-transcription.md)

## Context

The realtime layer could propose consequential actions but could not complete
them. A proposal created a pending action; committing it required pressing a
button on screen. That left the headline claim — hands-free operation at a bench,
by someone whose hands are occupied and gloved — one step short for exactly the
operations that matter: recording a measurement, completing a step, skipping one.

The obvious fix is to give the realtime model a `confirm_pending_action` tool. It
is also the wrong one. The model that produced the proposal would then be able to
approve it, and a model that hallucinates a tool call at the wrong moment would
write to a laboratory notebook. Everything else in this architecture exists to
prevent precisely that.

The difficulty is that "a human said yes" has to be established by a server that
receives only what a browser chooses to send. A `confirmed: true` field is an
assertion. A client timestamp is an assertion. A transcript supplied by the
browser is an assertion. None of them distinguish a person speaking from a model
emitting plausible arguments.

## Decision

A consequential action is committed only by
`POST /api/v1/pending-actions/{id}/spoken-confirmation`, whose evidence is **an
audio recording of the operator answering**, and which enforces four properties
deterministically:

1. **The evidence is a microphone recording, not a function argument.** Anything
   expressible as a function call is something the realtime model could issue by
   itself, so the realtime tool surface has no confirmation tool at all, and the
   safety evaluation asserts it never gains one. Note carefully what this does
   and does not establish: it establishes that the *model* cannot supply the
   confirmation through the channel it controls. It is **not** a proof of human
   liveness - see Consequences.

2. **The recording must be new.** Its SHA-256 is compared against every utterance
   already stored for the session; a match is refused with
   `CONFIRMATION_AUDIO_REPLAYED`. This stops a byte-identical clip being
   resubmitted. It does **not** stop a re-encoded copy, a different recording of
   the same words, or the same words played through a speaker - exact hashing
   cannot, and claiming otherwise would overstate it.

2a. **Recording may not begin until the readback has demonstrably finished.**
   Added on 18 August 2026 after the original implementation was found to start
   recording while the assistant was still speaking. The readback contains the
   exact phrase the backend accepts, so on a speaker-equipped machine the
   assistant's own voice could reach the microphone and satisfy the check.
   `say()` therefore returns a promise, and it resolves only when **all** of the
   following hold. Each clause was a hole in the first version of this fix, and
   each is covered by its own regression test:

   - **Correlation.** A unique identifier is put on `response.create` under
     `response.metadata`, and the provider echoes it on `response.created` and
     `response.done`. Only a response carrying *that* identifier can bind or
     satisfy the waiter. The first version bound the waiter to whatever
     `response.created` arrived next, which an automatic VAD turn or a
     tool-call follow-up could win.
   - **Status.** `response.done` is emitted for *every* terminal state, so its
     arrival is not success. Only `status === "completed"` satisfies a
     readback; `cancelled`, `failed`, and `incomplete` reject, and a missing or
     unrecognised status rejects rather than being assumed benign.
   - **Response-scoped audio.** Audio activity is tracked per `response_id`, so
     an audio-done event for one response cannot mark another quiet. The
     undocumented WebRTC `output_audio_buffer.*` events are corroboration only.
   - **Positively observed silence.** Provider events describe streaming
     completion, not the browser's local output buffer. A working Web Audio
     level meter must observe the speaker go quiet, and then a short settle
     window runs as a debounce. If the monitor is unavailable, suspended,
     failed, or closed, spoken confirmation **fails closed** and the operator
     uses the on-screen control - a monitor that cannot measure is never read
     as silence.

   Any other outcome rejects, and a rejection means no recording is taken.

2b. **The provider hears nothing for the whole confirmation, not just the
   readback.** Added on 19 August 2026. The outgoing WebRTC track was detached
   during the readback and reattached the instant it finished - while the local
   recorder was still open. Default provider VAD can hear the operator finish
   speaking and generate a response by itself, so the assistant could start
   talking into the evidence recording. Detachment now spans one explicit
   transaction: readback, quiet verification, capture cue, and every
   millisecond the `MediaRecorder` is open. `RTCRtpSender.replaceTrack()` is
   awaited rather than discarded, a failure to detach aborts the confirmation
   before anything is spoken, and if the assistant becomes audible mid-recording
   the clip is discarded rather than uploaded.

3. **The wording is read by the high-integrity path** and, when a second adapter
   is configured, cross-checked against an independent reading. Two readings that
   disagree produce no canonical transcript, so the confirmation is ambiguous and
   nothing is committed.

4. **The decision is made in ordinary code.** `domain/confirmations.py` returns
   confirm, reject, or ambiguous, and resolves every uncertain case away from
   committing. An affirmation carrying a negation — "that is not correct" — is
   ambiguous, never a yes.

For a measurement, an affirmation alone is **not accepted**. The operator must
restate the value, and the restated number and unit are compared against the
proposal. Saying "yes" establishes that they replied; saying "ten millilitres"
establishes that they heard the same number the system is about to write down.
Only the second distinguishes agreement from a confident-sounding mishearing,
and the 5 mL/50 mL case is the one this whole design exists to prevent.

If the high-integrity adapter is not configured, spoken confirmation of a
measurement is refused rather than downgraded, and the operator uses the
on-screen control.

## Consequences

**Accepted costs.**

- Confirming a measurement takes longer than saying "yes", and an operator who
  says only "yes" is asked again. That is deliberate friction on the one
  operation where being wrong is expensive.
- The confirmation window is fixed (default five seconds) rather than
  ending when speech stops. A slow reply is cut off and re-asked. An open-ended
  window would keep the microphone recording after the operator finished, which
  is a worse trade in a shared room.
- Every consequential voice action costs an extra transcription call, and two
  when cross-checking is on.

- **A confirmation readback cannot be interrupted, and does not claim to be.**
  Because provider input is detached for the whole protected window, the
  provider receives no audio and `input_audio_buffer.speech_started` cannot be
  a genuine detection of the operator talking over the readback. Rather than
  act on an event the design cannot produce, the readback is treated as
  non-interruptible while input is suppressed. Speech during that window is
  never submitted as a confirmation - not because it is detected and rejected,
  but because no recorder is open. The operator is prompted again after the
  readback, or uses the on-screen control. Ordinary conversational barge-in,
  outside the confirmation cycle, still works and is still wired to the
  provider event.

**Not solved by this decision.**

- **This is not human-liveness attestation.** What the backend receives is an
  audio blob from a client it cannot authenticate. Within the loopback,
  single-trusted-operator threat model that LabJarvis supports, that is
  proportionate: the operator is the only party with access, and the control
  exists to stop a *model* acting unilaterally, not to stop the operator
  deceiving their own notebook. It would not be adequate for a multi-user or
  networked deployment, which SECURITY.md already places out of scope.
- Two transcription models can be wrong in the same direction. If a model
  mishears the readback the same way it misheard the original value, the check
  agrees and the wrong value is recorded. No amount of server-side logic detects
  correlated error; only measurement against real speech would.
- **Acoustic separation is unverified in the field.** The turn boundary and the
  provider isolation are enforced and tested against a scripted transport that
  models the room and a provider willing to answer on its own, and the
  microphone constraints request echo cancellation — but nobody has yet run
  this with real speakers in a real room. The self-echo path is therefore
  **resolved in deterministic implementation tests; live acoustic behaviour
  remains unverified.** Until silence after readback, a correct reply, a
  rejection, and speech over the readback have been checked with real
  speakers and microphones, the claim that "the assistant cannot confirm
  itself through the loudspeaker" remains a
  well-founded design claim rather than a measured one.
- **Spoken confirmation requires Web Audio.** Failing closed when the level
  meter is unavailable means a browser without a working `AudioContext` cannot
  confirm by voice at all. That is the intended trade: the on-screen control is
  always present, and a confirmation taken without established silence would be
  worth less than no confirmation.
- The isolated realtime path was tested with deterministic fixture audio.
  The transaction and decision logic are verified, but speaker echo and
  recognition beside laboratory noise still need a live acoustic check.

## Alternatives rejected

- **A `confirm_pending_action` realtime tool.** Lets the proposing model approve
  its own proposal. Rejected outright.
- **Trusting the realtime transcript of "yes".** The transcript arrives over the
  same channel as the tool call, from the same model. It is not independent
  evidence of anything.
- **A client-supplied "captured after" timestamp.** The browser asserts it; the
  server cannot check it.
- **A delay between speaking and recording.** A fixed wait is a guess about how
  long a sentence takes, and the sentence length varies with the value being
  read back. It fails exactly when the readback is longest, which is when the
  measurement is most complex.
- **Relying on browser echo cancellation.** Best-effort, device-dependent, and
  designed for conversational duplex rather than for keeping a specific phrase
  out of a specific recording. It is kept as defence in depth and is not the
  control.
- **A spoken numeric nonce ("say four seven two").** Verifiable and fresh, but it
  confirms that a person is present rather than that they agree with the value.
  A readback of the actual measurement proves both.
