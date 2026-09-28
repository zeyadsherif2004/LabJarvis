import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { successfulAction } from "../../actions";
import { isEditableTarget } from "../../media/pushToTalk";
import { activeSnapshot, fakeClient, readyFakeMode } from "../../test/fixtures";
import type { PendingAction, ProtocolRequirement, ToolDescriptor } from "../../types";
import { ReagentVerificationView } from "./ReagentVerificationView";
import { SterileModeView } from "./SterileModeView";

function view(overrides: Partial<Parameters<typeof SterileModeView>[0]> = {}) {
  const descriptors = new Map<string, ToolDescriptor>();
  const media = {
    start: vi.fn(() => Promise.resolve()),
    stop: vi.fn(() => Promise.resolve(new Blob(["FAKE:pause"], { type: "audio/webm" }))),
    cancel: vi.fn(() => Promise.resolve()),
  };
  const props = {
    client: fakeClient(),
    readiness: readyFakeMode,
    snapshot: activeSnapshot,
    toolRegistry: descriptors,
    activeStep: activeSnapshot.version.steps[0] ?? null,
    verification: null,
    message: null,
    media,
    busy: false,
    muted: false,
    onMutedChange: vi.fn(),
    onInvokeTool: vi.fn(() => Promise.resolve(successfulAction)),
    onTextCommand: vi.fn(() => Promise.resolve(successfulAction)),
    onAudioCommand: vi.fn(() => Promise.resolve(successfulAction)),
    onAudioConfirm: vi.fn(() => Promise.resolve(successfulAction)),
    onConfirm: vi.fn(() => Promise.resolve(successfulAction)),
    onAsk: vi.fn(),
    onVerify: vi.fn(() => Promise.resolve(successfulAction)),
    onConfirmVerification: vi.fn(() => Promise.resolve(successfulAction)),
    onReplay: vi.fn(),
    onRefresh: vi.fn(),
    onOpenNotebook: vi.fn(),
    ...overrides,
  } as Parameters<typeof SterileModeView>[0] & { media: typeof media };
  render(<SterileModeView {...props} />);
  return props;
}

async function hold(key: string, target: Element | Document = document.body) {
  await act(async () => {
    fireEvent.keyDown(target, { key, code: key });
    await Promise.resolve();
  });
  await act(async () => {
    fireEvent.keyUp(target, { key, code: key });
    await Promise.resolve();
  });
}

describe("global push-to-talk", () => {
  it("records while the key is held anywhere on the page, and submits on release", async () => {
    const props = view();

    await hold("F8");

    expect(props.media.start).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(props.onAudioCommand).toHaveBeenCalledTimes(1));
  });

  it("works with a foot pedal that emulates a configured key", async () => {
    window.localStorage.setItem("labjarvis.pushToTalkKey", "PageDown");
    const props = view();

    await hold("PageDown");

    await waitFor(() => expect(props.onAudioCommand).toHaveBeenCalledTimes(1));
    window.localStorage.removeItem("labjarvis.pushToTalkKey");
  });

  it("ignores the key while the operator is typing", async () => {
    const props = view();
    const field = screen.getByLabelText("Typed command");

    await hold("F8", field);

    expect(props.media.start).not.toHaveBeenCalled();
    expect(isEditableTarget(field)).toBe(true);
  });

  it("shows a visible and announced listening state", async () => {
    view();

    await act(async () => {
      fireEvent.keyDown(document.body, { key: "F8" });
      await Promise.resolve();
    });

    expect(await screen.findByText(/listening\.\.\. release to submit/i)).toBeInTheDocument();
    expect(screen.getByText("Microphone is listening")).toBeInTheDocument();
  });

  it("Escape cancels a held recording without submitting it", async () => {
    const props = view();

    await act(async () => {
      fireEvent.keyDown(document.body, { key: "F8" });
      await Promise.resolve();
    });
    await act(async () => {
      fireEvent.keyDown(document.body, { key: "Escape" });
      await Promise.resolve();
    });
    await act(async () => {
      fireEvent.keyUp(document.body, { key: "F8" });
      await Promise.resolve();
    });

    expect(props.media.cancel).toHaveBeenCalledTimes(1);
    expect(props.onAudioCommand).not.toHaveBeenCalled();
  });

  it("releasing after the window loses focus still stops capture", async () => {
    const props = view();

    await act(async () => {
      fireEvent.keyDown(document.body, { key: "F8" });
      await Promise.resolve();
    });
    await act(async () => {
      window.dispatchEvent(new Event("blur"));
      await Promise.resolve();
    });

    await waitFor(() => expect(props.media.stop).toHaveBeenCalledTimes(1));
  });

  it("answers a waiting proposal by voice instead of issuing a new command", async () => {
    const pending: PendingAction = {
      id: "action-1",
      session_id: activeSnapshot.session.id,
      intent: "RECORD_MEASUREMENT",
      payload: { kind: "temperature", value: "37", unit: "degC" },
      source: "USER_CONFIRMED_SPEECH",
      status: "PENDING",
      expected_session_version: activeSnapshot.session.version,
      created_at: "2026-07-16T12:00:00Z",
      resolved_at: null,
      correlation_id: "c",
      model_inference_id: "m",
    } as PendingAction;
    const props = view({ snapshot: { ...activeSnapshot, pending_action: pending } });

    await hold("F8");

    await waitFor(() => expect(props.onAudioConfirm).toHaveBeenCalledTimes(1));
    expect(props.onAudioCommand).not.toHaveBeenCalled();
  });
});

describe("voice-triggered label check", () => {
  const requirement: ProtocolRequirement = {
    requirement_id: "req-1",
    name: "ethanol",
    quantity: "10",
    unit: "mL",
    aliases: [],
  };

  it("counts down, captures three frames and submits them without any touch", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const onVerifyFrames = vi.fn(() => Promise.resolve(successfulAction));
    const speak = vi.fn();
    const props = {
      requirements: [requirement],
      progress: [],
      result: null,
      mode: "fake" as const,
      busy: false,
      onVerify: vi.fn(() => Promise.resolve(successfulAction)),
      onConfirm: vi.fn(() => Promise.resolve(successfulAction)),
      onVerifyFrames,
      speak,
      countdownMs: 10,
    };
    const { rerender } = render(<ReagentVerificationView {...props} autoCaptureRequest={0} />);

    rerender(<ReagentVerificationView {...props} autoCaptureRequest={1} />);

    await waitFor(() => expect(onVerifyFrames).toHaveBeenCalledTimes(1));
    const [frames, requirementId] = onVerifyFrames.mock.calls[0] as unknown as [Blob[], string];
    expect(frames).toHaveLength(3);
    expect(requirementId).toBe("req-1");
    expect(speak).toHaveBeenCalledWith(expect.stringMatching(/hold the ethanol label/i));
    vi.useRealTimers();
  });

  it("waits for the spoken countdown then sends one hosted frame without probing", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      let speaking = true;
      const onVerifyFrames = vi.fn(() => Promise.resolve(successfulAction));
      const onProbe = vi.fn(() => Promise.resolve(null));
      const speak = vi.fn();
      const props = {
        requirements: [requirement],
        progress: [],
        result: null,
        mode: "fake" as const,
        busy: false,
        onVerify: vi.fn(() => Promise.resolve(successfulAction)),
        onConfirm: vi.fn(() => Promise.resolve(successfulAction)),
        onVerifyFrames,
        onProbe,
        speak,
        isSpeaking: () => speaking,
        hostedSingleCapture: true,
        countdownMs: 10,
      };
      const { rerender } = render(<ReagentVerificationView {...props} autoCaptureRequest={0} />);
      rerender(<ReagentVerificationView {...props} autoCaptureRequest={1} />);

      expect(speak).toHaveBeenCalledWith(expect.stringMatching(/three, two, one/i));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(45);
      });
      expect(onVerifyFrames).not.toHaveBeenCalled();
      speaking = false;
      await waitFor(() => expect(onVerifyFrames).toHaveBeenCalledTimes(1));
      const [frames, requirementId, capture] = onVerifyFrames.mock.calls[0] as unknown as [
        Blob[], string, { mode: string },
      ];
      expect(frames).toHaveLength(1);
      expect(requirementId).toBe("req-1");
      expect(capture.mode).toBe("single");
      expect(onProbe).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("lets local Vision use a saved timed single-frame choice for voice checks", async () => {
    window.localStorage.clear();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const onVerifyFrames = vi.fn(() => Promise.resolve(successfulAction));
      const onProbe = vi.fn(() => Promise.resolve(null));
      const speak = vi.fn();
      const props = {
        requirements: [requirement],
        progress: [],
        result: null,
        mode: "fake" as const,
        busy: false,
        onVerify: vi.fn(() => Promise.resolve(successfulAction)),
        onConfirm: vi.fn(() => Promise.resolve(successfulAction)),
        onVerifyFrames,
        onProbe,
        liveScan: { requiredConsecutive: 3, intervalMs: 100, timeoutMs: 1_000 },
        speak,
        isSpeaking: () => false,
        countdownMs: 10,
      };
      const view = render(<ReagentVerificationView {...props} autoCaptureRequest={0} />);

      expect(screen.getByRole("radio", { name: /live scan/i })).toBeChecked();
      fireEvent.click(screen.getByRole("radio", { name: /timed single frame/i }));
      expect(screen.getByRole("radio", { name: /timed single frame/i })).toBeChecked();
      view.rerender(<ReagentVerificationView {...props} autoCaptureRequest={1} />);

      await waitFor(() => expect(onVerifyFrames).toHaveBeenCalledTimes(1));
      const [frames, requirementId, capture] = onVerifyFrames.mock.calls[0] as unknown as [
        Blob[], string, { mode: string },
      ];
      expect(frames).toHaveLength(1);
      expect(requirementId).toBe("req-1");
      expect(capture.mode).toBe("single");
      expect(onProbe).not.toHaveBeenCalled();
      expect(speak).toHaveBeenCalledWith(expect.stringMatching(/three, two, one/i));

      view.unmount();
      render(<ReagentVerificationView {...props} autoCaptureRequest={0} />);
      expect(screen.getByRole("radio", { name: /timed single frame/i })).toBeChecked();
      fireEvent.click(screen.getByRole("radio", { name: /live scan/i }));
      expect(screen.getByRole("radio", { name: /live scan/i })).toBeChecked();
      expect(screen.getByRole("button", { name: /scan label until steady/i })).toBeEnabled();
    } finally {
      vi.useRealTimers();
      window.localStorage.clear();
    }
  });

  it("sends no hosted image if the spoken countdown never finishes", async () => {
    vi.useFakeTimers();
    try {
      const onVerifyFrames = vi.fn(() => Promise.resolve(successfulAction));
      const props = {
        requirements: [requirement],
        progress: [],
        result: null,
        mode: "fake" as const,
        busy: false,
        onVerify: vi.fn(() => Promise.resolve(successfulAction)),
        onConfirm: vi.fn(() => Promise.resolve(successfulAction)),
        onVerifyFrames,
        speak: vi.fn(),
        isSpeaking: () => true,
        hostedSingleCapture: true,
        countdownMs: 10,
      };
      const { rerender } = render(<ReagentVerificationView {...props} autoCaptureRequest={0} />);
      rerender(<ReagentVerificationView {...props} autoCaptureRequest={1} />);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(10_200);
      });

      expect(onVerifyFrames).not.toHaveBeenCalled();
      expect(screen.getByRole("alert")).toHaveTextContent(/No image was sent/i);
    } finally {
      vi.useRealTimers();
    }
  });

  it("offers a safe recapture after a result", () => {
    render(
      <ReagentVerificationView
        requirements={[requirement]}
        progress={[]}
        result={{
          id: "v1",
          session_id: "s",
          step_id: "step-1",
          requirement_id: "req-1",
          expected_label: "ethanol",
          observed_label: "ETHANOL | METHANOL",
          outcome: "UNCERTAIN",
          score: 60,
          decision: "PENDING",
        } as never}
        mode="fake"
        busy={false}
        onVerify={vi.fn()}
        onConfirm={vi.fn()}
        onVerifyFrames={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: /recapture \(3 frames\)/i })).toBeEnabled();
  });
});
