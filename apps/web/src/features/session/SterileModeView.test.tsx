import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { successfulAction } from "../../actions";
import { activeSnapshot, fakeClient, readyFakeMode } from "../../test/fixtures";
import type { SessionSnapshot, SessionTimer, ToolDescriptor } from "../../types";
import { SterileModeView } from "./SterileModeView";

function renderView(overrides: Partial<Parameters<typeof SterileModeView>[0]> = {}) {
  const toolNames = [
    "pause_session",
    "resume_session",
    "complete_step",
    "skip_step_with_reason",
    "end_session",
    "set_timer",
    "pause_timer",
    "resume_timer",
    "cancel_timer",
    "answer_protocol_question",
  ];
  const descriptors = new Map<string, ToolDescriptor>(
    toolNames.map((name) => [
      name,
      {
        name,
        version: "1",
        kind: "mutating",
        confirmation: "model_proposals_only",
        summary: name,
        legal_session_states: ["READY", "ACTIVE", "PAUSED"],
        required_parameters: [],
        realtime_exposed: true,
      },
    ]),
  );
  const props = {
    client: fakeClient(),
    readiness: readyFakeMode,
    snapshot: activeSnapshot,
    toolRegistry: descriptors,
    activeStep: activeSnapshot.version.steps[0] ?? null,
    verification: null,
    message: null,
    media: { start: vi.fn(), stop: vi.fn(() => Promise.resolve(new Blob())) },
    busy: false,
    muted: false,
    onMutedChange: vi.fn(),
    onInvokeTool: vi.fn(() => Promise.resolve(successfulAction)),
    onTextCommand: vi.fn(() => Promise.resolve(successfulAction)),
    onAudioCommand: vi.fn(() => Promise.resolve(successfulAction)),
    onConfirm: vi.fn(() => Promise.resolve(successfulAction)),
    onAsk: vi.fn(() =>
      Promise.resolve({
        ok: true as const,
        value: {
          text: "",
          abstained: true,
          citation: null,
          page: null,
          stepId: null,
          relevance: null,
        },
      }),
    ),
    onVerify: vi.fn(() => Promise.resolve(successfulAction)),
    onConfirmVerification: vi.fn(() => Promise.resolve(successfulAction)),
    onReplay: vi.fn(),
    onRefresh: vi.fn(),
    onOpenNotebook: vi.fn(),
    ...overrides,
  };
  render(<SterileModeView {...props} />);
  return props;
}

const runningTimer: SessionTimer = {
  id: "timer-1",
  session_id: "session-1",
  step_id: "step-1",
  label: "incubation",
  duration_seconds: 600,
  state: "RUNNING",
  remaining_seconds: 540,
  started_at: "2026-07-16T12:06:00Z",
  deadline_at: "2026-07-16T12:16:00Z",
  paused_at: null,
  resolved_at: null,
};

describe("SterileModeView", () => {
  it("uses a single timed capture when Vision is hosted", () => {
    renderView({
      readiness: {
        ...readyFakeMode,
        mode: "hosted",
        stages: {
          vision: {
            provider: "openai",
            provider_label: "OpenAI",
            locality: "hosted",
            model: "gpt-5.4-nano",
            detail: "",
            warmup: "not_applicable",
          },
        },
      },
      onVerifyFrames: vi.fn(),
    });

    expect(screen.getByRole("button", { name: /check label \(1 frame\)/i })).toBeInTheDocument();
    expect(screen.getByText(/sent to the configured Vision provider/i)).toBeInTheDocument();
  });
  it("shows the active step with its source reference", () => {
    renderView();

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Add 10 mL ethanol.");
    // The page reference appears on the active step and again in the rail.
    expect(screen.getAllByText(/Page 1/).length).toBeGreaterThan(0);
  });

  it("routes session control buttons through the tool gateway, not a text command", async () => {
    // A button already states its intent unambiguously, so sending it through
    // text understanding would add latency and a failure mode for no benefit.
    const props = renderView();

    await userEvent.click(screen.getByRole("button", { name: /pause session/i }));

    expect(props.onInvokeTool).toHaveBeenCalledWith("pause_session");
    expect(props.onTextCommand).not.toHaveBeenCalled();
  });

  it("completes a step through the gateway", async () => {
    const props = renderView();

    await userEvent.click(screen.getByRole("button", { name: /complete current step/i }));

    expect(props.onInvokeTool).toHaveBeenCalledWith("complete_step");
  });

  it("disables a bench action omitted by the server tool registry", () => {
    renderView({ toolRegistry: new Map() });

    expect(
      screen.getByRole("button", { name: /complete current step/i }),
    ).toBeDisabled();
  });

  it("requires a written reason before a step can be skipped", async () => {
    const props = renderView();

    await userEvent.click(screen.getByRole("button", { name: /skip step with a reason/i }));
    const reason = screen.getByLabelText(/why is this step being skipped/i);
    await userEvent.type(reason, "Reagent unavailable on the bench");
    await userEvent.click(
      screen.getByRole("button", { name: /skip step and record the reason/i }),
    );

    expect(props.onInvokeTool).toHaveBeenCalledWith("skip_step_with_reason", {
      reason: "Reagent unavailable on the bench",
    });
  });

  it("renders durable timers supplied by the server", () => {
    renderView({ snapshot: { ...activeSnapshot, timers: [runningTimer] } });

    expect(screen.getByText("09:00")).toBeInTheDocument();
    expect(screen.getAllByText(/incubation/).length).toBeGreaterThan(0);
  });

  it("keeps typed input available when microphone permission is denied", async () => {
    const denied = {
      start: vi.fn(() => Promise.reject(new DOMException("no", "NotAllowedError"))),
      stop: vi.fn(() => Promise.resolve(new Blob())),
    };
    const props = renderView({ media: denied });

    await userEvent.click(screen.getByRole("button", { name: /hold to speak/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/permission denied/i);

    await userEvent.type(screen.getByLabelText(/typed command/i), "what is the current step");
    await userEvent.click(screen.getByRole("button", { name: /submit command/i }));
    expect(props.onTextCommand).toHaveBeenCalledWith("what is the current step");
  });

  it.each([
    {
      name: "typed command",
      open: null,
      label: /typed command/i,
      value: "keep this command",
      submit: /submit command/i,
      override: "onTextCommand" as const,
    },
    {
      name: "timer duration",
      open: null,
      label: /start a timer \(seconds\)/i,
      value: "90",
      submit: /start timer/i,
      override: "onInvokeTool" as const,
    },
    {
      name: "skip reason",
      open: /skip step with a reason/i,
      label: /why is this step being skipped/i,
      value: "sample contaminated",
      submit: /skip step and record the reason/i,
      override: "onInvokeTool" as const,
    },
  ])("preserves $name when the mutation reports failure", async (scenario) => {
    const mutation = vi.fn().mockResolvedValue({ ok: false, code: "NETWORK_ERROR" });
    renderView({ [scenario.override]: mutation });
    if (scenario.open !== null) {
      await userEvent.click(screen.getByRole("button", { name: scenario.open }));
    }
    const input = screen.getByLabelText(scenario.label);
    await userEvent.type(input, scenario.value);
    await userEvent.click(screen.getByRole("button", { name: scenario.submit }));

    await waitFor(() => expect(mutation).toHaveBeenCalled());
    expect(
      screen.getByLabelText<HTMLInputElement>(scenario.label).value,
    ).toBe(scenario.value);
  });

  it("summarises an aborted session honestly instead of showing bench controls", () => {
    const aborted: SessionSnapshot = {
      ...activeSnapshot,
      session: { ...activeSnapshot.session, state: "ABORTED" },
    };
    renderView({ snapshot: aborted });

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(/ended before/i);
    expect(screen.getByText(/never performed/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /complete current step/i })).toBeNull();
  });

  it("does not offer hands-free voice when the deployment has it disabled", () => {
    renderView();

    expect(screen.getByText(/not enabled on this installation/i)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /turn on hands-free voice/i }),
    ).toBeNull();
  });
});
