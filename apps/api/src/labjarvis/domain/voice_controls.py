"""Listener and speech-output controls a gloved operator can say out loud.

These phrases steer the *interface* - whether LabJarvis is listening, whether it
speaks, repeating or interrupting what it said, silencing a timer alarm - and
never the experimental record. None of them is a proposal, so none needs a
confirmation, and none of them is ever sent to a language model: they are
matched here by exact phrase, before interpretation.

Two listening modes restrict what is accepted at all:

* **standby** (after "stop listening"): only "start listening" is honoured;
  every other segment is discarded without interpretation or retention;
* **barge-in** (a segment that began while LabJarvis was speaking or an alarm
  was sounding): only "stop"/"quiet", "mute" and "stop the alarm" are
  honoured - an operator naturally talks over an alarm to silence it. The
  echo guard still applies to everything else, so LabJarvis' own speech can
  never become a command, a confirmation, or a proposal - at worst it could
  interrupt itself or silence an alarm.
"""

from __future__ import annotations

import re
from enum import StrEnum


class VoiceControl(StrEnum):
    STOP_LISTENING = "STOP_LISTENING"
    START_LISTENING = "START_LISTENING"
    MUTE_SPEECH = "MUTE_SPEECH"
    UNMUTE_SPEECH = "UNMUTE_SPEECH"
    REPEAT_LAST_SPOKEN = "REPEAT_LAST_SPOKEN"
    STOP_SPEAKING = "STOP_SPEAKING"
    ACKNOWLEDGE_ALARM = "ACKNOWLEDGE_ALARM"


class ListenMode(StrEnum):
    #: Ordinary listening: every intent, including controls.
    COMMAND = "command"
    #: After "stop listening": only "start listening".
    STANDBY = "standby"
    #: A segment that started while LabJarvis spoke: stop/quiet, mute, stop the alarm.
    BARGE_IN = "barge_in"


_PHRASES: dict[str, VoiceControl] = {
    "stop listening": VoiceControl.STOP_LISTENING,
    "stop listening please": VoiceControl.STOP_LISTENING,
    "pause listening": VoiceControl.STOP_LISTENING,
    "start listening": VoiceControl.START_LISTENING,
    "resume listening": VoiceControl.START_LISTENING,
    "listen again": VoiceControl.START_LISTENING,
    "mute": VoiceControl.MUTE_SPEECH,
    "mute speech": VoiceControl.MUTE_SPEECH,
    "be silent": VoiceControl.MUTE_SPEECH,
    "unmute": VoiceControl.UNMUTE_SPEECH,
    "unmute speech": VoiceControl.UNMUTE_SPEECH,
    "speak again": VoiceControl.UNMUTE_SPEECH,
    "say that again": VoiceControl.REPEAT_LAST_SPOKEN,
    "say it again": VoiceControl.REPEAT_LAST_SPOKEN,
    "repeat that": VoiceControl.REPEAT_LAST_SPOKEN,
    "what did you say": VoiceControl.REPEAT_LAST_SPOKEN,
    "what'd you say": VoiceControl.REPEAT_LAST_SPOKEN,
    "come again": VoiceControl.REPEAT_LAST_SPOKEN,
    "stop": VoiceControl.STOP_SPEAKING,
    "stop talking": VoiceControl.STOP_SPEAKING,
    "quiet": VoiceControl.STOP_SPEAKING,
    "be quiet": VoiceControl.STOP_SPEAKING,
    "shush": VoiceControl.STOP_SPEAKING,
    "stop the alarm": VoiceControl.ACKNOWLEDGE_ALARM,
    "stop alarm": VoiceControl.ACKNOWLEDGE_ALARM,
    "silence the alarm": VoiceControl.ACKNOWLEDGE_ALARM,
    "alarm off": VoiceControl.ACKNOWLEDGE_ALARM,
    "timer done": VoiceControl.ACKNOWLEDGE_ALARM,
    "timer is done": VoiceControl.ACKNOWLEDGE_ALARM,
    "acknowledge the alarm": VoiceControl.ACKNOWLEDGE_ALARM,
    "acknowledge alarm": VoiceControl.ACKNOWLEDGE_ALARM,
}

#: What each listening mode may act on. Anything else is discarded untraced.
ALLOWED_IN_MODE: dict[ListenMode, frozenset[VoiceControl]] = {
    ListenMode.COMMAND: frozenset(VoiceControl),
    ListenMode.STANDBY: frozenset({VoiceControl.START_LISTENING}),
    ListenMode.BARGE_IN: frozenset(
        {VoiceControl.STOP_SPEAKING, VoiceControl.MUTE_SPEECH, VoiceControl.ACKNOWLEDGE_ALARM}
    ),
}

#: What LabJarvis says after each control. Never contains the wake phrase, so
#: an echo of it can never be mistaken for an addressed command. Empty means
#: "say nothing" (interrupting speech should be silent).
CONTROL_RESPONSES: dict[VoiceControl, str] = {
    VoiceControl.STOP_LISTENING: (
        "Listening paused. Say the wake word, then start listening, to resume."
    ),
    VoiceControl.START_LISTENING: "Listening again.",
    VoiceControl.MUTE_SPEECH: "Speech muted. Results stay on screen.",
    VoiceControl.UNMUTE_SPEECH: "Speech is on.",
    VoiceControl.REPEAT_LAST_SPOKEN: "",
    VoiceControl.STOP_SPEAKING: "",
    VoiceControl.ACKNOWLEDGE_ALARM: "Alarm stopped.",
}

_LEADING_POLITENESS = ("please ", "ok ", "okay ", "now ")


def _normalise(body: str) -> str:
    text = " ".join(re.findall(r"[a-z']+", body.casefold()))
    changed = True
    while changed:
        changed = False
        for prefix in _LEADING_POLITENESS:
            if text.startswith(prefix):
                text = text[len(prefix) :]
                changed = True
    return text.removesuffix(" please").strip()


def voice_control(body: str) -> VoiceControl | None:
    """The control this utterance body (wake phrase already removed) names, if any.

    Exact-phrase only: "stop the centrifuge" or "stop recording the volume" are
    not controls, and "stop" inside a longer sentence never interrupts anything.
    """

    return _PHRASES.get(_normalise(body))


def control_allowed(control: VoiceControl, mode: ListenMode) -> bool:
    return control in ALLOWED_IN_MODE[mode]
