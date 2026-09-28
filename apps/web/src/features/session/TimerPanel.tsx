import { useEffect, useState } from "react";
import { BellRing, Ban, Clock3, Pause, Play } from "lucide-react";

import { WarningBanner } from "../../components/WarningBanner";
import type { ProtocolStep, SessionTimer } from "../../types";
import { timerName } from "./timerAlarms";

interface TimerPanelProps {
  timers: SessionTimer[];
  /** Used to name a timer by its step ("timer for step 3"). */
  steps?: readonly ProtocolStep[];
  /** Silence sounding alarms; by voice this is "stop the alarm". */
  onAcknowledgeAlarm?: (() => void) | undefined;
  busy: boolean;
  canPause: boolean;
  canResume: boolean;
  canCancel: boolean;
  onPause: (timerId: string) => void;
  onResume: (timerId: string) => void;
  onCancel: (timerId: string) => void;
}

function formatDuration(seconds: number): string {
  const safe = Math.max(0, seconds);
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const remainder = safe % 60;
  const pad = (value: number) => value.toString().padStart(2, "0");
  return hours > 0
    ? `${hours}:${pad(minutes)}:${pad(remainder)}`
    : `${pad(minutes)}:${pad(remainder)}`;
}

/**
 * Tick a running timer down locally between server reads.
 *
 * The server value is authoritative - it is what survives a reload and what a
 * restarted backend recomputes from the stored deadline. This only smooths the
 * display between refreshes, and it re-seeds from every server response, so it
 * cannot drift away from the truth for long.
 */
function useLocalCountdown(timers: SessionTimer[]): Map<string, number> {
  const [offsetSeconds, setOffsetSeconds] = useState(0);
  const signature = timers
    .map((timer) => `${timer.id}:${timer.state}:${timer.remaining_seconds}`)
    .join("|");

  useEffect(() => {
    setOffsetSeconds(0);
  }, [signature]);

  useEffect(() => {
    const running = timers.some((timer) => timer.state === "RUNNING");
    if (!running) return undefined;
    const handle = window.setInterval(() => setOffsetSeconds((value) => value + 1), 1000);
    return () => window.clearInterval(handle);
  }, [signature, timers]);

  return new Map(
    timers.map((timer) => [
      timer.id,
      timer.state === "RUNNING"
        ? Math.max(0, timer.remaining_seconds - offsetSeconds)
        : timer.remaining_seconds,
    ]),
  );
}

export function TimerPanel({
  timers,
  steps = [],
  onAcknowledgeAlarm,
  busy,
  canPause,
  canResume,
  canCancel,
  onPause,
  onResume,
  onCancel,
}: TimerPanelProps) {
  const displayed = useLocalCountdown(timers);
  const active = timers.filter(
    (timer) => timer.state === "RUNNING" || timer.state === "PAUSED",
  );
  const elapsed = timers.filter((timer) => timer.state === "ELAPSED");
  const sounding = elapsed.filter((timer) => timer.alarm_active === true);

  return (
    <section className="timer-panel" aria-labelledby="timer-panel-title">
      <h2 id="timer-panel-title">
        <Clock3 aria-hidden="true" /> Timers
      </h2>

      {active.length === 0 && elapsed.length === 0 ? (
        <p className="timer-empty">
          No timers are running. Timers you set here survive a page reload.
        </p>
      ) : null}

      {sounding.length > 0 ? (
        // Text and an icon carry the alarm; colour is only reinforcement.
        <div className="timer-alarm" role="alert">
          <BellRing aria-hidden="true" />
          <strong>
            ALARM: {sounding.map((timer) => `Timer ${timerName(timer, steps)} is done`).join("; ")}
          </strong>
          <span>Say the wake word, then &quot;stop the alarm&quot;, or press Stop alarm.</span>
          {onAcknowledgeAlarm !== undefined ? (
            <button className="button button--primary" type="button" onClick={onAcknowledgeAlarm}>
              Stop alarm
            </button>
          ) : null}
        </div>
      ) : null}

      {elapsed.map((timer) => (
        <WarningBanner title={`Timer elapsed: ${timer.label}`} key={timer.id}>
          The {formatDuration(timer.duration_seconds)} interval has passed. Time elapsing
          does not complete the step - check the protocol and confirm the next action
          yourself.
        </WarningBanner>
      ))}

      {active.length > 0 ? (
        <ul className="timer-list">
          {active.map((timer) => {
            const remaining = displayed.get(timer.id) ?? timer.remaining_seconds;
            return (
              <li className={`timer timer--${timer.state.toLowerCase()}`} key={timer.id}>
                <div className="timer-readout">
                  <strong aria-live="off">{formatDuration(remaining)}</strong>
                  <span>
                    {timer.label}
                    {timer.state === "PAUSED" ? " (paused)" : ""}
                  </span>
                </div>
                <div className="timer-controls">
                  {timer.state === "RUNNING" ? (
                    <button
                      className="button button--secondary"
                      type="button"
                  disabled={busy || !canPause}
                      onClick={() => onPause(timer.id)}
                    >
                      <Pause aria-hidden="true" /> Pause
                      <span className="visually-hidden"> the {timer.label} timer</span>
                    </button>
                  ) : (
                    <button
                      className="button button--secondary"
                      type="button"
                  disabled={busy || !canResume}
                      onClick={() => onResume(timer.id)}
                    >
                      <Play aria-hidden="true" /> Resume
                      <span className="visually-hidden"> the {timer.label} timer</span>
                    </button>
                  )}
                  <button
                    className="button button--secondary"
                    type="button"
                disabled={busy || !canCancel}
                    onClick={() => onCancel(timer.id)}
                  >
                    <Ban aria-hidden="true" /> Cancel
                    <span className="visually-hidden"> the {timer.label} timer</span>
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      ) : null}
    </section>
  );
}
