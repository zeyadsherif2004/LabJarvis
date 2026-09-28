# Operator user guide

## Before entering sterile work

Use LabJarvis only with an authorized, current protocol and the required supervision, risk assessment, SDS/emergency procedures, training, and independent recordkeeping. The assistant can mishear, misread, or misinterpret. It never authorizes a step or certifies a reagent.

1. Start LabJarvis and read **System check**.
2. Confirm the execution profile and, per stage, which model runs where (the header chips and **Diagnostics -> Model stages** say "on this machine" or "hosted by <provider>"), model readiness and warm-up, raw-media/transcript retention, and the configured wake phrase.
3. Use **Test microphone** and **Test camera**. Resolve permission/device issues before gloving; typed/image-upload fallbacks remain available.
4. Choose your push-to-talk key (or map a USB foot pedal to it) on the session screen or in **Settings -> This browser**, and decide whether to use hands-free listening (see below).

## Choosing how LabJarvis runs (Settings)

Open **Settings** to choose **Local** (everything on this computer), **Hosted**
(OpenAI), **Hybrid** (speech and label reading here, command text on OpenAI) or
**Custom** (pick each stage). Hosted stages also need **Allow hosted processing**
and an API key configured by whoever set up LabJarvis; the page says exactly
what is missing. After **Save configuration**, either the page says
**Configuration active**, or **Configuration saved — restart required** with
**Restart LabJarvis** / **Restart later**. Restart before gloving up: during an
active session LabJarvis asks you to confirm, because the screen stops for a few
seconds (recorded work is kept). If the page says to restart manually, close
LabJarvis and start it again the usual way.

**Voice output** chooses who speaks: the Kokoro natural voice (recommended, on
this computer) with a voice picker and **Play sample**, or the browser voice.
If Kokoro is not installed or fails, the browser voice speaks instead.

## Import and approve a protocol

1. Upload your protocol as it is - PDF, Word (DOCX), plain text (TXT, UTF-8), or Markdown - within the displayed limits, or use the visibly labelled development sample in fake mode. It does not need reformatting: numbered steps (typed or Word list numbering), bulleted steps, numbered table rows, sub-steps, wrapped lines, and WARNING/CAUTION/NOTE lines are recognised, and a Procedure/Method section is told apart from Materials, Reagents, and References. For an image-only PDF, select **Use OCR for a scanned PDF** before choosing the file. OCR is bounded, slower, and its page text is marked `OCR-derived`; never enable it to bypass a failed ordinary import without reviewing the result.
2. Review every proposed title, instruction, order, safety note, duration, reagent/quantity/unit, alias, and source excerpt against the original document.
   - **Check before approval** flags mark what the document did not settle: ranges ("1-2 mL"), approximate or unmeasurable amounts, time ranges, "overnight", more than one duration in a step, repetitions, conditions, restarted or skipped numbering, warnings whose step is ambiguous, and OCR text. LabJarvis sets no value it would have to guess - a range gets no quantity and an unclear time gets no timer. Tick each flag once you have compared it with the source; approval is refused while any flag is unticked.
   - **Source text not used as steps** lists everything import kept but did not place in a step (introductions, materials and reference lists, page headers and footers). Check that none of it is an instruction the protocol needs.
3. Add, remove, reorder, or edit the proposal as necessary. Extraction can be wrong; LabJarvis does not infer omitted steps. A step or reagent you add starts with placeholder text, and approval is refused until you replace it.
4. Optionally enter a non-identifying **Run label** such as `batch 7, replicate A`. Do not enter a participant, patient, or personal identifier.
5. Save the reviewed draft, then approve it. Approval freezes that version. Copy an approved version to create an editable draft when the procedure must change later.
6. Create and start the session only after review. The first reviewed step becomes active.

Approval accepts at most 500 steps, 100 reagent requirements and 100 safety notes per
step, and 50 aliases per requirement. Durations must be from 1 second through 7 days.
Quantities must be finite, nonnegative, no greater than 1,000,000,000, and paired with
a unit. These are software integrity limits, not scientific suitability guidance.

## Sterile-mode interaction

The current step, safety notes, expected inputs/reagents, session status, visible response, and append-only activity remain on screen. The same grounded response is spoken (Kokoro voice, or the browser voice as fallback) and can be muted/replayed.

### Input methods

- **Push to talk:** hold **Hold to speak**, or hold the configured key (F8 by default; F9, F10, F12, Pause, ScrollLock or PageDown) anywhere on the session page - a USB foot pedal that sends that key works the same way. Release to submit; capture stops after 30 seconds; Escape cancels. The key is ignored while you are typing in a field.
- **Hands-free listening (optional, enabled by the operator's configuration):** press **Start hands-free listening** once. The browser detects speech locally and sends each segment to LabJarvis, which ignores anything that does not start with the wake phrase ("Jarvis, ..."). Outside a session it switches off after 10 minutes without speech; **while a session is open it stays on**. Push-to-talk keeps working.
- **Hands-free mode (Settings, or `LABJARVIS_HANDS_FREE_PROFILE=true`):** opening a session shows one large **Tap once to start hands-free** button. Tap it before gloving up: it asks for microphone and camera permission and starts listening. From then on the whole bench workflow can be run by voice, and every outcome is spoken as well as shown. With Local Vision, choose **Live scan** (default) or **Timed single frame** under **Local camera check** before gloving up; the choice is saved in this browser and the same "check reagent" voice command uses it. Hosted Vision waits for "check reagent," speaks a three-two-one countdown, then sends one cropped frame to the configured provider; no continuous probes are sent. The observed label is read back before a voice-only decision when it is safe to speak. With `python scripts/tasks.py hands-free` (kiosk launch on this computer) not even that tap is needed; the page says **Kiosk mode** when that is active.
- **Listener and speech controls (say the wake word first):** "stop listening" (only "start listening" is then recognised, and only while the LabJarvis window has focus), "start listening", "mute", "unmute", "say that again", "stop" or "quiet" (interrupts what LabJarvis is saying), "stop the alarm" or "timer done". None of these records anything.
- **If the microphone fails** LabJarvis says so, retries three times, then asks for a tap.
- **Typed command:** always available and recommended for recovery/precision.
- **Protocol questions:** ask them by voice ("Jarvis, how long do I incubate the sample?") or type them into **Ask the approved protocol**. The spoken answer names the step and quotes it ("Step 2 says: ..."). When nothing in the approved protocol is relevant enough, LabJarvis says "I couldn't find that in the approved protocol" and never guesses. Retrieval is lexical: an off-topic question that shares a word with a step (for example "the boiling point of ethanol") can still be answered with that step, so check the quoted step.

Use one action per command. Speak values and units explicitly. Review the transcript and proposal; reject anything inaccurate.

### Supported commands

Examples are illustrative, not scientific instructions:

| Goal | Example |
|---|---|
| Record measurement | “Jarvis, record the temperature as 37 degrees Celsius.” |
| Record observation | “Record an observation: the solution is clear.” |
| Get grounded guidance | “What is the current step?”, “What is the next step?”, “Repeat the instruction.” |
| Session control | “Start the session” (approved, not started), “Pause session”, “Resume the session”, “Complete step”, “End the session.” |
| Timer | “Set a timer for 5 minutes”, “How long is left?”, “Pause the timer”, “Resume the timer”, “Cancel the 10 minute timer”, “Cancel the timer for step 2.” With several timers LabJarvis asks which. |
| Timer alarm | When a timer finishes LabJarvis says "Timer ... is done" with a tone, every 20 seconds, until you say “Stop the alarm” / “Timer done” or press **Stop alarm**. Warnings at 1 minute and 10 seconds (unless muted). |
| Skip a step | “Skip this step because the reagent is unavailable.” A reason is required; without one LabJarvis asks for it. |
| Correct a value | “Correct the last measurement to 12 millilitres”, “Correct the volume to 12 mL.” Only measurements; the original entry is kept and linked. Other corrections use the session **Notebook**. |
| Read back | “What did I just record?”, “Where am I?”, “What reagents do I need?” |
| Reagent check | “Check reagent”, “Check the ethanol”, “Check reagent two.” With several reagents you must name one; an unknown name gets the list read out. Local Vision uses the selected **Local camera check** mode: live scan seeks three agreeing readings among the latest five, while timed single frame speaks a countdown and takes one local OCR reading. Hosted Vision speaks a countdown and captures one cropped frame on request; no live probes are sent. A provider request may be retried if it fails. Every pending reading still needs your confirmation. |
| Help | “Help”, “What can you do?” |

Broad scientific questions, deviations, compound/ambiguous actions, missing values/units, and unsupported requests should be rejected without changing state.

## Confirmations and session versions

Measurements, observations, timers, step completion, pause/resume, and early end are proposals when they come from speech or text. Read the confirmation panel, then choose **Confirm action** or **Reject**, or answer by voice:

- **With push-to-talk:** say "reject", or "confirm". For a measurement a bare "confirm"/"yes" is refused - say the value back ("confirm 10 millilitres"). Spoken read-back needs the high-integrity transcriber to be configured; otherwise use the buttons.
- **Voice only (if enabled):** start with the wake phrase and repeat what is being confirmed: "Jarvis, confirm 10 millilitres", "Jarvis, confirm 10 minutes", "Jarvis, confirm step 3", "Jarvis, confirm skip step 2", "Jarvis, confirm 12 millilitres" (a correction's new value), "Jarvis, confirm cancel 10 minute timer", "Jarvis, confirm pause timer", "Jarvis, confirm start session", "Jarvis, confirm resume session", "Jarvis, confirm pause"; or "Jarvis, reject". The exact phrase is spoken with the proposal and shown under it. A bare "yes", another value or another action never commits.
- **Observations by voice (only if `LABJARVIS_VOICE_OBSERVATIONS_ENABLED`):** LabJarvis reads the text back ("I heard: the solution turned cloudy"); say "Jarvis, confirm observation". It is accepted only if both transcriptions of your dictation agreed. This is weaker than repeating a value - listen to the read-back before confirming.

Anything unclear leaves the proposal waiting. Direct buttons (pause/resume, complete step, skip with a reason, timer controls, end session) act immediately because the button already states the intent. A stale confirmation fails if another state change occurred. Ending before normal completion records the session as `ABORTED`, not `COMPLETED`.

## Reagent-label verification

1. Check that the expected reagent shown came from the reviewed active step.
2. Present only the relevant physical label clearly: good lighting, minimal glare, straight view, readable type.
3. Capture/upload the image or say "check reagent" for the selected hands-free capture. With Local Vision choose **Live scan** or **Timed single frame** first, then use **Scan label until steady** or **Check label (1 frame)**; the choice survives a reload in this browser. When local live scanning is unavailable, **Check label (3 frames)** remains the fallback. Hosted Vision uses **Check label (1 frame)**. On a step with several reagents, choose which one you are holding first. LabJarvis shows the outcome (`MATCH`/`MISMATCH`/`UNCERTAIN`), expected and observed text and the comparison score; thresholds and model provenance appear in the session **Notebook**. A single reading can misread a still label, so inspect the physical label before confirming.
4. Independently inspect the physical label. Confirm, reject, or correct the OCR text (**Confirm visible reading** / **Reject OCR proposal**). Correcting recomputes the deterministic outcome and records the decision. Confirming a `MISMATCH` or `UNCERTAIN` reading records that reading; it is shown as "reviewed - label does NOT match", never as a completed check. In voice-only mode only a `MATCH` can be confirmed by voice ("Jarvis, confirm ethanol"); any reading can be rejected ("Jarvis, reject").
5. Treat `MATCH` only as visible text similarity. Check concentration, lot, expiry, integrity, contents, contamination, and chemical suitability through the actual procedure/SDS and human inspection.

When the output is uncertain, wrong, incomplete, or physically inconsistent, stop and ask a supervisor—do not adjust thresholds during the session.

## Notebook, correction, and export

The activity/review view links confirmed entries to inference adapter/model, confidence, latency, timestamps, correlation IDs, verifications, and audit events. To correct a record, enter the replacement payload; LabJarvis appends a correction pointing to the original rather than deleting it.

Export JSON for the versioned, machine-readable session aggregate: session and reviewed
protocol version, correction lineage, model inferences (including failures), reagent-label
reviews, timers, audit events, utterances, and pending/resolved/expired action history. The
document identifies itself as `labjarvis.session` schema `2.0` and includes the configured
application commit when available. CSV is explicitly a flattened notebook-entry subset for
human tabular review, identified as `2.0-csv-notebook-subset`; it does not preserve the full
nested provenance graph. Protect exports as laboratory data. They do not automatically
replace an institutionally approved notebook.

## Finding and managing work

The dashboard searches protocol titles and filters sessions by state, run label,
date range, and exact protocol-version ID. It hides archived protocols by
default. During a refresh, visible older cards are temporarily disabled until
the newest query is authoritative; this prevents a stale result opening the
wrong run.

An approved version cannot be edited. Use **Copy as new draft** for a new
version. Archive an obsolete protocol to hide it from normal discovery; restore
it from the archived filter. Archiving never changes an existing session or the
version preserved in its notebook.

Open **Data** from the application navigation for data operations. Export a
session before destructive work. Session deletion requires typing `DELETE
<session-id>` exactly and removes the local aggregate plus retained raw media;
the generated deletion receipt contains no content. Protocol deletion is
allowed only when referential integrity permits it. A referenced protocol must
remain as historical provenance and can be archived instead.

The same screen creates a consistent SQLite backup and validates a selected
backup into a staging directory. Validation does not activate the restore.
Stop the application and follow the offline restore procedure in the
[deployment guide](operations/local-deployment.md). PostgreSQL operators must
use `pg_dump`/`pg_restore`; the in-app SQLite backup operation is refused.

Deleting active local data does not delete prior exports, backup copies,
synchronized files, screenshots, runtime traces, or data already sent to a
hosted provider. Handle every copy under the approved retention/withdrawal
plan. LabJarvis has no automatic age-based purge.

## Failure recovery

- **Permission denied:** use typed/image-file fallback and fix permission later.
- **Low confidence/ambiguity/unsupported:** restate one short explicit action or type it; confirm no entry/state changed.
- **Model unavailable/timeout:** preserve the physical procedure and independent notes; use diagnostics and retry only when safe.
- **Stale/conflict:** reload current session state before submitting another decision.
- **Wrong extraction/expected reagent:** do not continue; the approved protocol version must be reviewed outside the active workflow.
- **Application crash:** follow institutional procedure and independent recordkeeping. LabJarvis has no high-availability guarantee.
