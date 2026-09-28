# ADR 0020: Kokoro as the local neural voice, browser speech as fallback

- **Status:** Accepted (2026-09-28). Extends ADR 0018 (eyes-free output).
- **Code:** `infrastructure/speech_output.py`, `api/routes/speech_output.py`,
  `media/speechOutput.ts`, `features/settings/SettingsView.tsx`,
  `scripts/setup_models.py kokoro`.

## Context

Every outcome is spoken so a gloved researcher need not look at the screen.
Speech used the browser's `speechSynthesis` with the system default voice,
which on Windows is a classic SAPI voice and sounds robotic. Better browser
voices ("Natural", Google) are rendered online, which would send spoken text off
the machine. A hosted TTS API would do the same and cost per reply.

## Decision

1. **Kokoro-82M (Apache-2.0) through `kokoro-onnx` 0.6.1 on ONNX Runtime**, run
   by the LabJarvis server. The ONNX build needs no PyTorch and, unlike the
   `kokoro` package, no runtime download of a spaCy model: it is fully offline
   once `python scripts/setup_models.py kokoro` (or `tasks.py models`) has
   fetched the two pinned files, whose size and SHA-256 are verified before
   they replace anything.
2. **Full-precision model.** Measured on the project laptop CPU (16 threads,
   four typical replies x3): fp32 median 1.0 s per message, real-time factor
   0.36; the int8 export was slower here (3.6 s, RTF 1.26), so it is not used.
   Through the API a one-sentence reply takes ~0.66 s after warm-up.
3. **Low latency by design:** the model is warmed in the background at startup
   when Kokoro is chosen; the browser sends one sentence per request and fetches
   the next while the current one plays, so speech starts after the first
   sentence rather than the whole reply. One synthesis runs at a time.
4. **Browser speech is the fallback, never silence.** If Kokoro is not
   installed, not ready, the audio context is blocked, or a request fails, the
   rest of that reply is spoken by the browser voice, and Kokoro is retried
   after 30 s. `stop` (barge-in) aborts requests and cuts audio at once, and
   `isSpeaking` covers Kokoro audio so the hands-free echo guard still works.
5. **Configuration** follows ADR 0019: Settings offers *Kokoro (recommended)*
   or *Browser voice*, a voice (English Kokoro voices) with a sample preview,
   and saves them with the other choices, applied at restart. A saved
   configuration defaults to Kokoro; the bare environment default stays
   `browser` so tests and demos are unchanged.
6. **Not part of the record.** Synthesised audio is never stored and no
   provenance is written; the text is what the screen already shows. The
   endpoint accepts only text (max 1000 characters) and, for previews, a voice
   name; no paths, models or other fields.

## Consequences

- Natural speech with no hosted dependency and nothing leaving the machine.
- About 350 MB of model files and ~0.5 GB RAM when loaded; CPU only (a GPU
  build of ONNX Runtime would conflict with faster-whisper's CPU one).
- Replies start ~0.5-1 s later than the instant browser voice on this CPU.
