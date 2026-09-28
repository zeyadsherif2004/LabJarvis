import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { HandsFreeOptions } from "../../media/handsFreeListener";
import { briefText, createSpeechOutput } from "../../media/speechOutput";
import type { PendingAction, ProtocolStep, SessionSnapshot, SessionTimer } from "../../types";
import { HandsFreeListeningPanel } from "./HandsFreeListeningPanel";
import { TimerPanel } from "./TimerPanel";
import { alarmText, timerName, useTimerAlarms } from "./timerAlarms";
import { adjectivalDuration, voiceConfirmationPhrase } from "./voicePhrase";

afterEach(() => {
  vi.useRealTimers();
});

function scriptedListener() {
  let options: HandsFreeOptions | null = null;
  const create = vi.fn((given: HandsFreeOptions) => {
    options = given;
    return Promise.resolve({
      start: () => Promise.resolve(),
      pause: () => Promise.resolve(),
      destroy: () => Promise.resolve(),
    });
  });
  return {
    create,
    speechStart: () => options?.onSpeechStart?.(),
    segment: (audio: Blob) => options?.onSegment(audio),
    fail: () => options?.onError?.("the microphone stopped"),
  };
}

describe("hands-free listener lifecycle", () => {
  it("starts by itself after the one gesture, with no idle timeout in a session", async () => {
    vi.useFakeTimers();
    const listener = scriptedListener();
    const onSegment = vi.fn();
    render(
      <HandsFreeListeningPanel
        wakePhrase="Jarvis"
        busy={false}
        onSegment={onSegment}
        createListener={listener.create}
        isSpeaking={() => false}
        autoStart
        idleTimeoutMs={null}
      />,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(listener.create).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/Listening for "Jarvis/)).toBeInTheDocument();
    // An hour of silence: still listening.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60 * 60_000);
    });
    expect(screen.getByText(/Listening for "Jarvis/)).toBeInTheDocument();
    act(() => listener.segment(new Blob(["FAKE:Jarvis, next step"])));
    expect(onSegment).toHaveBeenCalledWith(expect.any(Blob), { listenMode: "command" });
  });

  it("still switches off after the idle timeout outside a session", async () => {
    vi.useFakeTimers();
    const listener = scriptedListener();
    render(
      <HandsFreeListeningPanel
        wakePhrase="Jarvis"
        busy={false}
        onSegment={vi.fn()}
        createListener={listener.create}
        isSpeaking={() => false}
        autoStart
        idleTimeoutMs={1_000}
      />,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(screen.getByText(/switched off after a period without speech/)).toBeInTheDocument();
  });

  it("sends segments in standby mode after stop listening", async () => {
    const listener = scriptedListener();
    const onSegment = vi.fn();
    render(
      <HandsFreeListeningPanel
        wakePhrase="Jarvis"
        busy={false}
        onSegment={onSegment}
        createListener={listener.create}
        isSpeaking={() => false}
        standby
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /start hands-free listening/i }));
    expect(await screen.findByText(/only "Jarvis, start listening" is recognised/)).toBeInTheDocument();
    act(() => listener.segment(new Blob(["x"])));
    expect(onSegment).toHaveBeenCalledWith(expect.any(Blob), { listenMode: "standby" });
  });

  it("routes speech that overlapped LabJarvis to the barge-in channel only", async () => {
    const listener = scriptedListener();
    const onSegment = vi.fn();
    const onBargeIn = vi.fn();
    let speaking = true;
    render(
      <HandsFreeListeningPanel
        wakePhrase="Jarvis"
        busy={false}
        onSegment={onSegment}
        onBargeIn={onBargeIn}
        createListener={listener.create}
        isSpeaking={() => speaking}
        echoGuardMs={0}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /start hands-free listening/i }));
    await screen.findByText(/Listening for|paused while/);
    act(() => listener.speechStart());
    speaking = false;
    act(() => listener.segment(new Blob(["FAKE:Jarvis, stop"])));
    // Never a command or an answer; only the barge-in channel sees it.
    expect(onSegment).not.toHaveBeenCalled();
    expect(onBargeIn).toHaveBeenCalledTimes(1);
  });

  it("announces a microphone failure and retries with backoff before asking for a tap", async () => {
    vi.useFakeTimers();
    let attempts = 0;
    const announce = vi.fn();
    const create = vi.fn(() => {
      attempts += 1;
      return Promise.reject(new Error("no microphone"));
    });
    render(
      <HandsFreeListeningPanel
        wakePhrase="Jarvis"
        busy={false}
        onSegment={vi.fn()}
        createListener={create}
        isSpeaking={() => false}
        autoStart
        announce={announce}
        retryDelaysMs={[100, 200, 400]}
      />,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(attempts).toBe(4); // the first start plus three retries
    expect(announce).toHaveBeenCalledWith("Listening stopped: microphone problem. Retrying.");
    expect(announce).toHaveBeenLastCalledWith(
      "Hands-free listening could not restart. Tap the screen once to start it again.",
    );
    expect(screen.getByText(/could not restart/)).toBeInTheDocument();
  });

  it("recovers when a retry succeeds", async () => {
    vi.useFakeTimers();
    let attempts = 0;
    const create = vi.fn(() => {
      attempts += 1;
      if (attempts === 1) return Promise.reject(new Error("busy device"));
      return Promise.resolve({
        start: () => Promise.resolve(),
        pause: () => Promise.resolve(),
        destroy: () => Promise.resolve(),
      });
    });
    render(
      <HandsFreeListeningPanel
        wakePhrase="Jarvis"
        busy={false}
        onSegment={vi.fn()}
        createListener={create}
        isSpeaking={() => false}
        autoStart
        announce={vi.fn()}
        retryDelaysMs={[100]}
      />,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    expect(screen.getByText(/Listening for "Jarvis/)).toBeInTheDocument();
  });
});

describe("speech output", () => {
  it("keeps the outcome and what to say in brief mode", () => {
    const text =
      "Proposed: volume 10 millilitres in step 1. Review and confirm before it is applied. To confirm, say the wake word, then: confirm 10 millilitres. Or reject.";
    expect(briefText(text)).toBe(
      "Proposed: volume 10 millilitres in step 1. To confirm, say the wake word, then: confirm 10 millilitres.",
    );
  });

  it("stops, repeats and respects mute", () => {
    const spoken: string[] = [];
    const cancel = vi.fn();
    vi.stubGlobal("speechSynthesis", {
      speaking: false,
      cancel,
      speak: (utterance: { text: string }) => spoken.push(utterance.text),
    });
    vi.stubGlobal(
      "SpeechSynthesisUtterance",
      class {
        public constructor(public text: string) {}
      },
    );
    const output = createSpeechOutput();
    output.speak("Recorded 10 millilitres for volume in step 1.");
    output.stop();
    expect(cancel).toHaveBeenCalled();
    output.repeat();
    expect(spoken).toEqual([
      "Recorded 10 millilitres for volume in step 1.",
      "Recorded 10 millilitres for volume in step 1.",
    ]);
    output.setMuted(true);
    output.speak("Muted text");
    expect(spoken).toHaveLength(2);
    output.setMuted(false);
    output.repeat();
    expect(spoken.at(-1)).toBe("Muted text");
    vi.unstubAllGlobals();
  });
});

const steps = [{ id: "s1", number: 1 }, { id: "s2", number: 2 }] as unknown as ProtocolStep[];

function timer(overrides: Partial<SessionTimer>): SessionTimer {
  return {
    id: "t1",
    session_id: "session",
    step_id: "s2",
    label: "timer",
    duration_seconds: 600,
    state: "RUNNING",
    remaining_seconds: 600,
    started_at: "2026-09-27T09:00:00+00:00",
    deadline_at: "2026-09-27T09:10:00+00:00",
    paused_at: null,
    resolved_at: null,
    ...overrides,
  };
}

describe("timer alarms", () => {
  it("names a timer by its label or its step, never with the wake phrase", () => {
    expect(timerName(timer({ label: "incubation" }), steps)).toBe("incubation, step 2");
    expect(timerName(timer({}), steps)).toBe("step 2");
    expect(alarmText(timer({}), steps)).toBe(
      "Timer step 2 is done. Say the wake word, then stop the alarm.",
    );
    expect(alarmText(timer({}), steps).toLowerCase()).not.toContain("jarvis");
  });

  it("sounds and speaks until the server reports it acknowledged", () => {
    vi.useFakeTimers();
    const speak = vi.fn();
    const playTone = vi.fn();
    const props = {
      steps,
      enabled: true,
      repeatMs: 20_000,
      warningSeconds: [60, 10],
      muted: false,
      speak,
      playTone,
      onDeadline: vi.fn(),
    };
    const { rerender } = renderHook((timers: SessionTimer[]) => useTimerAlarms({ ...props, timers }), {
      initialProps: [timer({ state: "ELAPSED", remaining_seconds: 0, alarm_active: true })],
    });
    expect(playTone).toHaveBeenCalledTimes(1);
    expect(speak).toHaveBeenCalledWith("Timer step 2 is done. Say the wake word, then stop the alarm.");
    act(() => {
      vi.advanceTimersByTime(40_000);
    });
    expect(playTone).toHaveBeenCalledTimes(3);
    rerender([timer({ state: "ELAPSED", remaining_seconds: 0, alarm_active: false })]);
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(playTone).toHaveBeenCalledTimes(3);
  });

  it("warns before the deadline and asks the server to settle it", () => {
    vi.useFakeTimers();
    const speak = vi.fn();
    const onDeadline = vi.fn();
    renderHook(() =>
      useTimerAlarms({
        timers: [timer({ remaining_seconds: 70 })],
        steps,
        enabled: true,
        repeatMs: 20_000,
        warningSeconds: [60, 10],
        muted: false,
        speak,
        playTone: vi.fn(),
        onDeadline,
      }),
    );
    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(speak).toHaveBeenLastCalledWith("Timer step 2: 1 minute left.");
    act(() => {
      vi.advanceTimersByTime(50_000);
    });
    expect(speak).toHaveBeenLastCalledWith("Timer step 2: 10 seconds left.");
    act(() => {
      vi.advanceTimersByTime(11_000);
    });
    expect(onDeadline).toHaveBeenCalledTimes(1);
  });

  it("shows a text alarm, not only a colour, with a stop button", () => {
    const onAcknowledgeAlarm = vi.fn();
    render(
      <TimerPanel
        timers={[timer({ state: "ELAPSED", remaining_seconds: 0, alarm_active: true })]}
        steps={steps}
        onAcknowledgeAlarm={onAcknowledgeAlarm}
        busy={false}
        canPause
        canResume
        canCancel
        onPause={vi.fn()}
        onResume={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByText(/ALARM: Timer step 2 is done/).closest("[role=alert]")).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Stop alarm" }));
    expect(onAcknowledgeAlarm).toHaveBeenCalledTimes(1);
  });
});

describe("hands-free confirmation phrases", () => {
  const snapshot = {
    session: { active_step_id: "s2" },
    version: { steps },
  } as unknown as SessionSnapshot;
  const action = (intent: string, payload: Record<string, string> = {}) =>
    ({ intent, payload }) as unknown as PendingAction;

  it("mirrors the server's read-back phrases", () => {
    expect(voiceConfirmationPhrase(action("SKIP_STEP_WITH_REASON"), snapshot)).toBe(
      "confirm skip step 2",
    );
    expect(
      voiceConfirmationPhrase(action("CORRECT_PREVIOUS_ENTRY", { value: "12", unit: "mL" }), snapshot),
    ).toBe("confirm 12 millilitres");
    expect(
      voiceConfirmationPhrase(action("CANCEL_TIMER", { timer_duration_seconds: "600" }), snapshot),
    ).toBe("confirm cancel 10 minute timer");
    expect(voiceConfirmationPhrase(action("PAUSE_TIMER"), snapshot)).toBe("confirm pause timer");
    expect(voiceConfirmationPhrase(action("START_SESSION"), snapshot)).toBe("confirm start session");
    expect(voiceConfirmationPhrase(action("RESUME_SESSION"), snapshot)).toBe(
      "confirm resume session",
    );
    expect(adjectivalDuration(5400)).toBe("1 hour 30 minute");
  });

  it("offers an observation phrase only when opted in and both readings agreed", () => {
    const agreed = action("RECORD_OBSERVATION", { heard_agreement: "dual_agreed" });
    expect(voiceConfirmationPhrase(agreed, snapshot)).toBeNull();
    expect(voiceConfirmationPhrase(agreed, snapshot, { voiceObservations: true })).toBe(
      "confirm observation",
    );
    expect(
      voiceConfirmationPhrase(action("RECORD_OBSERVATION"), snapshot, { voiceObservations: true }),
    ).toBeNull();
  });
});
