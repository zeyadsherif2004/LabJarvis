# Real-world session: preparing a saline solution (28 September 2026)

On 28 September I ran LabJarvis at a bench for the first time, speaking to it
through a microphone and showing labels to the webcam, with a
simple procedure and nothing scripted. Up to then every test had been
automated or synthetic: Playwright journeys with fake models, and
text-to-speech clips played through the real models. Those showed that the
logic holds together. They could not show how it copes with my voice in an
ordinary room.

I built the system and ran the session myself, so this is not a user study
and it says nothing about how a newcomer would get on. It is still the only
evidence so far of how speech recognition, label reading and the
confirmation rules behave with real input, and it turned up problems the
synthetic testing never did.

All figures below come from the session export
([`session-2026-09-28-saline.json`](session-2026-09-28-saline.json), with the
CSV notebook subset alongside) and are recomputed by
[`analyse_session.py`](analyse_session.py) into
[`session-2026-09-28-metrics.json`](session-2026-09-28-metrics.json). Times are
UTC, as stored.

## Setup

The protocol was *Preparation of a 2% Saline Solution*, an eight-step Word
document I wrote for the test: wipe the bench, add 100 mL water, add 2 g
salt, stir 2 min, record the temperature, settle 3 min, record an
observation, label and store. Only water and table salt were involved. The
"reagent label" was a sheet of paper with SODIUM CHLORIDE handwritten on one
side and SUGAR on the other.

I used hands-free listening with the wake word "Jarvis" in a normal browser
window rather than the kiosk launch. Voice-only confirmation was on. Voice
confirmation of observations was off, so the observation had to be confirmed
on screen or with push-to-talk.

Speech was transcribed by OpenAI `gpt-4o-mini-transcribe`. Every utterance
was also transcribed separately by `gpt-4o-transcribe` (for part of the
session, `gpt-4o-mini-transcribe` again) as a cross-check. Commands went
through the rule grammar first, with DeepSeek (`deepseek-flash`) as the
fallback. Labels were read by EasyOCR on the laptop's GPU at first, and later
by OpenAI `gpt-5.4-nano`.

### The build changed during the session

This needs saying up front because it affects how the numbers can be used.
The session stayed open for over four hours while problems it exposed were
being fixed, and some of those fixes went in while it was still running:

- the live label-scan rule changed from `stable-consecutive-v1` to
  `stable-asymmetric-v3` between the first and second scan attempts;
- label reading was switched from local EasyOCR to hosted vision before the
  fourth attempt, and the verification transcriber was switched between
  `gpt-4o-mini-transcribe` and `gpt-4o-transcribe`;
- the code that compares the two transcriptions and reads back spoken numbers
  (`domain/transcripts.py`) was patched at about 03:53, in the middle of the
  temperature confirmation described below.

The export does not record an application commit, so the exact code behind
each event cannot be reconstructed. The run is therefore a *real-conditions
walkthrough of a changing build*, not a measurement of one fixed version.

## How it went

### Starting and the first two steps (00:19-00:26)

"Jarvis, start the
session" produced a proposal, and "Jarvis, confirm start session" started it.
"What is the current step?", "What's the next step?" and "Help" all got the
right answer first time. I set a one-minute timer by voice ("set a timer for
one minute", then "confirm one minute"), asked how long was left (46 seconds),
and when it ran out I silenced the alarm by voice 11.7 seconds later. Step 1
was completed and confirmed by voice. "What reagents do I need?" correctly
said 100 mL distilled water.

Completing step 2 took five tries. Three times the cross-check refused the
command because the two transcriptions disagreed: one heard "complete step",
the other "komplet step" or "komplet sta". A fourth recording caught
"complete step" and "confirm step two" run together. Each time LabJarvis said
it had heard two different things and did nothing, which is what it is
designed to do, but repeating a two-word command four times was frustrating.

### Step 3 and the label check (00:26-03:38)

Both protocol questions were
answered by quoting the right step ("Step 3 says: Add 2 g sodium chloride to
the water"). The reagent check is where most of the session's time went. I
asked LabJarvis to check the sodium chloride 18 times across roughly three
hours, most of them while the capture problems were being worked on.

Five readings were actually completed:

| Time | Reader | What it read | Score (match needs 92) | Result | My decision |
|---|---|---|---|---|---|
| 00:48 | EasyOCR, live scan | `Sodium Chlofid €` | 89.7 | MISMATCH | rejected |
| 02:05 | EasyOCR, live scan | `SoovM CHLo RIOE'` | 73.3 | MISMATCH | rejected |
| 02:06 | EasyOCR, live scan | `SooruM CHLo RIOE` | 77.4 | MISMATCH | rejected |
| 03:07 | gpt-5.4-nano, one frame | `SODIUM CHLORIDE` | 100 | MATCH | confirmed |
| 03:11 | gpt-5.4-nano, one frame (SUGAR side) | `Suc t i a` | 33.3 | MISMATCH | rejected |

EasyOCR never produced a correct reading of the handwritten label. The first
attempt came close (89.7, just under the 92 needed), and all three scans agreed
across their frames, so the consensus rule behaved correctly: it reported a
consistent mismatch rather than letting one lucky frame through. The problem
was the reading, not the decision. Hosted vision read the same label
perfectly, and turning the paper over to SUGAR produced a clear mismatch,
which I rejected. No mismatch was ever confirmed, and the only confirmed
reading was a genuine match.

### Steps 4 and 5 and recording the temperature (03:38-04:23)

"How long do I
stir the solution?" was answered from step 4. "Record the temperature" with no
value was refused as incomplete, as intended. "Record the temperature as
twenty-one degrees Celsius" became a correct proposal (21 degC) through the
DeepSeek fallback, because the rule grammar only reads numbers written as
digits.

Confirming that proposal took 19 spoken attempts over three proposals and 33
minutes, and this is the most important finding of the session. Two separate
faults were responsible:

1. The main transcriber wrote numbers as words ("twenty-one") and the
   verification transcriber wrote them as digits ("21"). The cross-check
   compared them literally, saw "no number" against "21" and refused. That
   happened on 11 utterances (10 confirmations and one repeated command).
2. When both transcribers *did* write "twenty-one", the read-back parser split
   it into two numbers (20 and 1) and refused because the reply seemed to
   contain more than one value. That happened three times, on replies that
   were perfectly clear.

The very first reply was "confirm twenty-five", the deliberate wrong value
from the test plan, and it was refused. I rejected the first two proposals myself after the repeated
failures. The third was finally confirmed at 04:13 with a reply that both
transcribers happened to write as digits. Nothing was ever recorded that I
had not confirmed, but nobody at a real bench would put up with this.

The rest of step 5 went smoothly. "What did I just record?" read back 21
degrees Celsius. The first correction attempt ("correct the last measurement
to twenty-two degrees Celsius") was refused, because the DeepSeek fallback
returned the correction without its value and LabJarvis asked for the value
rather than guessing. Saying "22" with digits produced the right proposal, and
"confirm twenty-two degrees Celsius", now spelled out, was accepted. By then
the transcript fix was in place. The correction was stored as a new entry
linked to the original 21 degC reading, which is still in the notebook.

### Steps 6 to 8 (04:23-04:30)

These went through in about seven minutes
with no problems at all. The session was paused and resumed by voice. "Skip
this step" without a reason was refused with a prompt for one; with a reason
it became a proposal, which I rejected by voice, and the step was then
completed normally. The observation ("the solution is clear and colorless")
was confirmed. Steps 7 and 8 were completed by voice and the session finished
as COMPLETED.

## What worked

- The full protocol ran by voice from start to finish. All eight steps were
  completed and none skipped, and every state change (start, eight step
  completions, pause, resume, timer, measurement, correction, observation) came
  from spoken commands plus a spoken or on-screen confirmation.
- Nothing was recorded that should not have been. 18 proposals: 15
  accepted, three rejected by me. Every accepted proposal was something I meant
  to do. Every unclear, conflicting or wrong-valued reply (16 of them) left the
  proposal waiting and changed nothing.
- Guidance was accurate. All 10 read-only requests (current and next step,
  help, timer status, expected reagents, two protocol questions, repeat, last
  entry, progress) got the correct answer, and both protocol questions were
  answered by quoting the approved step.
- Refusals were right. An incomplete command, a skip without a reason and
  a correction without a usable value were all refused with a clear prompt
  instead of a guess.
- The timer worked end to end by voice, including silencing the alarm.
- Protocol import and review held up. The Word document came in as eight
  steps with the three timers and both reagents. The warning flag on step 3 was
  acknowledged before approval, and the introduction, materials list and notes
  were kept as unused source text. This was checked in the database, because
  the export does not yet include them (see below).
- Failures were contained. Eight of 237 model calls failed (two 20-second
  transcription timeouts, two provider-unavailable errors, four empty or
  invalid transcriptions). Each one produced a message and no state change.
- Speed was fine when things understood each other. Transcription took
  about 1 second per utterance (median), the rule grammar under 2 ms, the
  DeepSeek fallback about 0.85 s, EasyOCR about 80 ms per frame and hosted
  vision about 1.4 s.

## What went wrong

| Problem | Effect in this session | Cause | Status now |
|---|---|---|---|
| Numbers written as words by one transcriber and as digits by the other | 11 utterances refused; 33 minutes to record one temperature | the cross-check compared the texts literally | **Fixed during the session** (`transcripts.py` now treats "twenty-one" and "21" as the same; checked against these exact transcripts) |
| "twenty-one" read back as two numbers | three perfectly clear confirmations refused | compound number words split by the read-back parser | **Fixed during the session** (the same replay now confirms) |
| Accent and phonetic spellings in the verification transcript ("komplet", "rekord", "Žarvis", Arabic script for "Jarvis") | 8 commands and confirmations refused; step 2 took five tries | the second transcriber produced phonetic or non-English spellings despite the English language setting | **Mitigated after the session.** English was already requested, so the verification transcriber is now also given the wake word and command words as a prompt (never protocol terms or numbers, so it stays independent). Not yet re-tested with real speech |
| Spelled numbers skip the rule grammar | measurement and timer went through the LLM; one correction lost its value there and had to be repeated | the grammar only matches digits | **Fixed after the session.** Quantities spoken as words are now read by the grammar directly; the session's phrasings are replayed in the tests |
| EasyOCR could not read the handwritten label | three consistent mismatches on the right label; hosted vision was needed for a match | EasyOCR suits printed labels; handwriting is out of scope | **Known limitation.** Real printed labels (H3) are still the proper test |
| Export omits import-review data | the export shows no flag on step 3, and no unused source text, although both are in the database | the export serialiser was not updated with the import changes | **Fixed after the session** |
| No application commit in the export | the build behind the session cannot be pinned | `LABJARVIS_APPLICATION_COMMIT` not set in development | **Open** (configuration) |

## How this compares with the synthetic evidence

| | Synthetic and automated (before) | This session |
|---|---|---|
| Speech | two TTS voices, clean audio | my own voice, real microphone and room |
| Transcription disagreement | occasional ("pause the timer" in one voice) | 22 of 60 utterances (37%) |
| Unintended commits | 0 | 0 |
| Wrong read-back values refused | 78/78 | 1/1 (plus 16 unclear replies left waiting) |
| Full protocol by voice | 28-37 s for scripted runs | about 64 minutes of active use (258 minutes wall clock) |
| Label reading | rendered, printed-style labels | handwritten paper; local OCR failed, hosted vision succeeded |
| Build | fixed | changed during the run |

So the safety side held up with real input, since nothing was committed
without a matching confirmation. Usability is another matter, at least with my
voice and this configuration. The rules that behave well on clean synthetic
audio get very repetitive once two transcribers start disagreeing about
spelling. Two of the three causes of disagreement were fixed during the
session. The third, spelling differences that come from my accent, is not
something the synthetic voices could ever have shown up, and it is still open.

## Limits of this evidence

- One session, one speaker, and that speaker built the system and knew
  exactly what to say. A new user would almost certainly do worse.
- The build and configuration changed mid-session, so the session is a
  sequence of short runs on slightly different versions, not one controlled
  trial.
- The session was open for 258 minutes but only about 64 were active. The rest
  are gaps of 15-72 minutes while problems were being fixed. Step 3 (192
  minutes) and step 5 (45 minutes) are inflated by those gaps; steps 6-8 took
  seven minutes together.
- Speech was hosted (OpenAI) rather than local, so this says nothing about the
  local Whisper configuration.
- Anything the system ignored (speech without the wake word, background
  conversation) is deliberately not stored, so false wakes cannot be counted
  from the export. The separate false-wake run in H11 is still needed.
- Not tried in this session: off-protocol questions, unsupported and compound
  commands, barge-in ("Jarvis, stop"), the kiosk launch, reloading mid-session,
  and typed commands.

## Details to add by hand

The export cannot record these, so they should be filled in from memory or
notes: room and background noise, microphone and webcam, browser, how many
times the screen or keyboard was touched after the start, and what was done
during each long gap.
