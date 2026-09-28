# Hands-free bench workflow - evaluation summary (2026-09-27)

No paid API calls. Raw results are the JSON files in this folder. These
particular tests used synthetic speech. The separate
[real-voice session](../real-world/session-2026-09-28-saline.md) used hosted
speech and was not part of these runs.

## 1. Fake/fixture (deterministic "FAKE:" speech, Playwright) - `zero-touch-e2e.json`

| Run | Non-voice interactions | Wake-phrase utterances | Result |
|---|---|---|---|
| Normal browser, hands-free profile | 1 tap (pointerdown + mousedown of one gesture), 0 after it | 35 | pass: open session, Q&A (answer + abstention), measurement (bare "yes" refused, value read-back committed), read-back of last entry, stop/start listening, reagent by name with live scan (match confirmed, mismatch refused then rejected), 2 s timer alarm silenced by voice, 10 min timer paused and cancelled, step completed, step skipped with reason, last measurement corrected, session ended |
| Kiosk flag (`--autoplay-policy=no-user-gesture-required`, `?kiosk=1` on localhost) | 0 | 4 | pass |

The default E2E pass (push-to-talk, pedal, on-screen controls, accessibility)
still passes: 27/27. Stateful property suite with the new rules: 1,000
sequences, 0 invariant violations (`../verification/property-1000.json`).

## 2. Real local models on synthetic inputs - `hands-free-evaluation.json`

Two Windows TTS voices; faster-whisper small.en (GPU) with base.en as the
cross-check; grammar v2 + qwen2.5:3b cascade; EasyOCR on rendered labels;
production `LabJarvisService` with the hands-free profile and opt-in voice
observations.

**A. Voice-only confirmation of the new intents (112 reply clips).**

| Intent | Command understood | Valid reply committed | False commits |
|---|---|---|---|
| skip step with reason | 16/16 | 2/2 | 0 |
| correct last measurement | 16/16 | 2/2 | 0 |
| cancel timer | 16/16 | 2/2 | 0 |
| pause timer | 8/16 | 1/2 | 0 |
| start session | 16/16 | 2/2 | 0 |
| resume session | 16/16 | 2/2 | 0 |
| observation (opt-in) | 16/16 | 2/2 | 0 |

Totals: 0 false commits; 0 false refusals of a valid reply to an understood
command; 13/13 rejections recorded; 78/78 bare ("yes", "confirm"), wrong,
second-value, unaddressed and echoed replies refused. The 8 misses are one
voice's "pause the timer", where small.en and base.en disagreed; the command
failed closed ("I heard that two different ways"), so the operator must repeat
it.

**B. Protocol questions.** Answerable: 8/10 answered with the right step
("Step N says: ..."); the two misses are one wording ("what goes into the
demonstration vessel?") in both voices, where retrieval v1 abstained. Unanswerable:
8/8 abstained ("I couldn't find that in the approved protocol"). Wrong answers: 0.

**C. Read-only queries and controls.** 26/26 (how long is left, reagents,
where am I, last entry, reagent by name, stop the alarm / timer done, stop and
start listening, mute, unmute, say that again, stop); 4/4 alarms silenced by
voice; 0 proposals created.

**D. Scripted full protocol through one session (28 steps per voice).**

| Voice | Commands | Re-asks | On-screen fallbacks after start | Time |
|---|---|---|---|---|
| Microsoft David | 30 | 2 | 1 (the early-end confirmation: readings disagreed twice) | 37.2 s |
| Microsoft Zira | 31 | 3 | 0 | 28.1 s |

Whisper heard "end the session" as "and the session", so the operator re-asked
with the documented alternative "finish the session" (both voices); one
"pause the timer" needed a repeat.

**Earlier voice-only intents, re-run after these changes**
(`voice-only-confirmation.json`): 58 clips, 0 false commits, 12/12 valid
commits - unchanged.

## 3. Grammar v2 compatibility - `grammar-v2-compatibility.json`

0 version-1 results changed on the 81-command benchmark and both held-out
sets. Newly matched: 2/81, 31/138, 29/139; none a state change the label did
not ask for (most are off-topic wh-questions now routed to protocol Q&A, which
abstains instead of the labelled UNSUPPORTED).

## 4. Self-recorded

None: `research/real_media/private/` does not exist.

## Defects found and fixed by this evaluation

- `10-minute` (small.en) vs `10 minute` (base.en) was treated as a conflict;
  digit-letter hyphens are now folded (`transcripts.py`).
- "end" is often heard as "and": "finish the session" and "confirm finish
  session" are accepted alternatives.
- (E2E) a stable live-scan status was never cleared, so a second spoken check in
  the same step did nothing (`ReagentVerificationView.tsx`, regression test).
- (E2E) "stop the alarm" said over the alarm tone was dropped by the echo guard;
  it is now accepted on the barge-in channel.
