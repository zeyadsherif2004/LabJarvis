import { useEffect, useRef } from "react";

import type { ProtocolStep, SessionTimer } from "../../types";
import { spokenDuration } from "./voicePhrase";

/** "the incubation timer" or "the timer for step 3". Never contains the wake phrase. */
export function timerName(timer: SessionTimer, steps: readonly ProtocolStep[]): string {
  const label = timer.label.trim();
  const step = steps.find((candidate) => candidate.id === timer.step_id);
  if (label !== "" && !["timer", "a timer"].includes(label.toLowerCase())) {
    return step === undefined ? label : `${label}, step ${step.number}`;
  }
  return step === undefined ? spokenDuration(timer.duration_seconds) : `step ${step.number}`;
}

export function alarmText(timer: SessionTimer, steps: readonly ProtocolStep[]): string {
  return `Timer ${timerName(timer, steps)} is done. Say the wake word, then stop the alarm.`;
}

interface TimerAlarmOptions {
  timers: SessionTimer[];
  steps: readonly ProtocolStep[];
  enabled: boolean;
  /** How often a sounding alarm repeats until it is acknowledged. */
  repeatMs: number;
  /** Seconds-remaining warnings, e.g. [60, 10]. */
  warningSeconds: number[];
  muted: boolean;
  speak: (text: string) => void;
  playTone: () => void;
  /**
   * Called just after a running timer's deadline, so the server settles it as
   * elapsed and reports the alarm. Alarms are never invented locally: they are
   * derived from server timer state, which is what makes them survive a reload.
   */
  onDeadline: () => void;
}

/**
 * Timer alarms and warnings, eyes-free.
 *
 * - A timer the server reports as `alarm_active` sounds a tone and speaks
 *   "Timer ... is done" immediately and every `repeatMs` until acknowledged
 *   ("Jarvis, stop the alarm" or the on-screen button). The tone plays even when
 *   speech is muted; the spoken sentence respects mute.
 * - Running timers get spoken warnings at the configured seconds remaining,
 *   unless muted.
 */
export function useTimerAlarms(options: TimerAlarmOptions): void {
  const { timers, steps, enabled, repeatMs, warningSeconds, muted, speak, playTone, onDeadline } =
    options;
  const latest = useRef({ speak, playTone, onDeadline, muted, steps });
  latest.current = { speak, playTone, onDeadline, muted, steps };
  const warned = useRef(new Set<string>());

  const sounding = timers.filter((timer) => timer.alarm_active === true);
  const soundingKey = sounding.map((timer) => timer.id).join(",");

  useEffect(() => {
    if (!enabled || sounding.length === 0) return undefined;
    const ring = () => {
      latest.current.playTone();
      const names = sounding.map((timer) => alarmText(timer, latest.current.steps));
      if (!latest.current.muted) latest.current.speak(names.join(" "));
    };
    ring();
    const handle = window.setInterval(ring, repeatMs);
    return () => window.clearInterval(handle);
    // The set of sounding timers is the trigger.
  }, [enabled, soundingKey, repeatMs]);

  const runningKey = timers
    .filter((timer) => timer.state === "RUNNING")
    .map((timer) => `${timer.id}:${timer.remaining_seconds}`)
    .join("|");

  useEffect(() => {
    if (!enabled) return undefined;
    const handles: number[] = [];
    for (const timer of timers) {
      if (timer.state !== "RUNNING") continue;
      handles.push(
        window.setTimeout(
          () => latest.current.onDeadline(),
          Math.max(0, timer.remaining_seconds * 1000 + 500),
        ),
      );
      for (const seconds of warningSeconds) {
        const key = `${timer.id}:${seconds}`;
        const delay = (timer.remaining_seconds - seconds) * 1000;
        if (delay < 0 || warned.current.has(key)) continue;
        handles.push(
          window.setTimeout(() => {
            warned.current.add(key);
            if (latest.current.muted) return;
            latest.current.speak(
              `Timer ${timerName(timer, latest.current.steps)}: ${spokenDuration(seconds)} left.`,
            );
          }, delay),
        );
      }
    }
    return () => handles.forEach((handle) => window.clearTimeout(handle));
    // Re-armed whenever the server's view of running timers changes.
  }, [enabled, runningKey, warningSeconds.join(",")]);
}
