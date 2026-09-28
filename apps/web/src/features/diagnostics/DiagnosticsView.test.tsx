import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { readyFakeMode, readyHostedMode, readyLocalMode } from "../../test/fixtures";
import { DiagnosticsView } from "./DiagnosticsView";


describe("DiagnosticsView", () => {
  it("names the hosted provider and the configured model per modality", () => {
    render(<DiagnosticsView degraded={false} onRetry={() => undefined} readiness={readyHostedMode} />);

    expect(screen.getByText("Hosted API")).toBeVisible();
    expect(screen.getAllByText("openai").length).toBeGreaterThan(0);
    // Each model is named in the summary list and again in the staged
    // readiness panel below it.
    expect(screen.getAllByText("gpt-4o-transcribe").length).toBe(2);
    expect(screen.getAllByText("gpt-5.6-terra").length).toBe(4);
  });

  it("states that model data leaves the machine in hosted mode", () => {
    render(<DiagnosticsView degraded={false} onRetry={() => undefined} readiness={readyHostedMode} />);

    expect(screen.getByText(/audio, transcript context, and label images are sent/i)).toBeVisible();
  });

  it("keeps local database retention distinct from provider-side retention", () => {
    render(<DiagnosticsView degraded={false} onRetry={() => undefined} readiness={readyHostedMode} />);

    expect(screen.getByText(/Local database retention/i)).toBeVisible();
    expect(
      screen.getByText(/provider-side retention is governed by the configured provider/i),
    ).toBeVisible();
  });

  it("states that nothing leaves the device in local mode", () => {
    render(<DiagnosticsView degraded={false} onRetry={() => undefined} readiness={readyLocalMode} />);

    expect(screen.getByText(/no model data leaves this device/i)).toBeVisible();
    expect(screen.queryByText(/Hosted provider/i)).toBeNull();
  });

  it("labels fake mode without implying real models", () => {
    render(<DiagnosticsView degraded={false} onRetry={() => undefined} readiness={readyFakeMode} />);

    expect(screen.getByText("Development fake")).toBeVisible();
    expect(screen.queryByText(/transmitted/i)).toBeNull();
    expect(screen.getAllByText(/not applicable/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/deterministic fixture/i).length).toBeGreaterThan(0);
  });

  it("renders bounded content-free latency aggregates", () => {
    render(<DiagnosticsView degraded={false} onRetry={() => undefined} readiness={readyFakeMode} />);

    expect(screen.getByRole("heading", { name: /recent model latency/i })).toBeVisible();
    expect(screen.getByText(/bounded to 200 stored inference records/i)).toBeVisible();
    expect(screen.getByText(/3 samples/i)).toBeVisible();
    expect(screen.queryByText(/private protocol value|spoken ethanol/i)).toBeNull();
  });

  it("never renders a credential field or value", () => {
    const { container } = render(<DiagnosticsView degraded={false} onRetry={() => undefined} readiness={readyHostedMode} />);

    expect(container.querySelector('input[type="password"]')).toBeNull();
    expect(screen.queryByLabelText(/api key/i)).toBeNull();
    expect(container.textContent).not.toMatch(/api[_ -]?key/i);
  });
});
