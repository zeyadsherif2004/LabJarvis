import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { successfulAction, type ActionResult } from "../../actions";
import { readyFakeMode, readyHostedMode, readyLocalMode } from "../../test/fixtures";
import { SetupView } from "./SetupView";


describe("SetupView", () => {
  it("offers a clearly labelled deterministic sample only in fake mode", async () => {
    const user = userEvent.setup();
    const onImport = vi
      .fn<(file: File, useOcr: boolean) => Promise<ActionResult>>()
      .mockResolvedValue(successfulAction);
    render(<SetupView readiness={readyFakeMode} busy={false} onImport={onImport} />);

    await user.click(screen.getByRole("button", { name: /load development sample/i }));

    expect(onImport).toHaveBeenCalledTimes(1);
    const file = onImport.mock.calls[0]?.[0];
    expect(file?.name).toBe("development-only-ethanol-protocol.pdf");
    expect(file?.type).toBe("application/pdf");
    expect(await file?.text()).toContain("%PDF-1.4");
    expect(screen.getByRole("button", { name: /test microphone/i })).toBeEnabled();
    expect(screen.getByText(/raw audio and images: discarded/i)).toBeVisible();
  });

  it("offers explicit bounded OCR for an image-only PDF", async () => {
    const user = userEvent.setup();
    const onImport = vi
      .fn<(file: File, useOcr: boolean) => Promise<ActionResult>>()
      .mockResolvedValue(successfulAction);
    render(<SetupView readiness={readyLocalMode} busy={false} onImport={onImport} />);

    await user.click(screen.getByRole("checkbox", { name: /use ocr for a scanned pdf/i }));
    const input = screen.getByLabelText("Protocol document");
    const file = new File(["%PDF"], "scan.pdf", { type: "application/pdf" });
    await user.upload(input, file);

    expect(onImport).toHaveBeenCalledWith(file, true);
    expect(screen.getByText(/ocr is slower and marks extracted source text as ocr-derived/i)).toBeVisible();
  });

  it("warns prominently that data is transmitted in hosted mode", () => {
    render(<SetupView readiness={readyHostedMode} busy={false} onImport={vi.fn()} />);

    expect(
      screen.getByRole("heading", { name: /external model processing is enabled/i }),
    ).toBeVisible();
    expect(
      screen.getByText(/audio, transcript context, and label images are transmitted to/i),
    ).toBeVisible();
    expect(screen.getByText(/openai/i)).toBeVisible();
  });

  it("says nothing is sent before the operator acts", () => {
    render(<SetupView readiness={readyHostedMode} busy={false} onImport={vi.fn()} />);

    expect(screen.getByText(/nothing is sent before you do/i)).toBeVisible();
  });

  it("does not claim provider-side retention is controlled locally", () => {
    render(<SetupView readiness={readyHostedMode} busy={false} onImport={vi.fn()} />);

    expect(
      screen.getByText(/governed by that account's terms, not by LabJarvis/i),
    ).toBeVisible();
    expect(
      screen.getByText(/do not affect what the hosted provider does/i),
    ).toBeVisible();
  });

  it("shows no external-processing warning in local mode", () => {
    render(<SetupView readiness={readyLocalMode} busy={false} onImport={vi.fn()} />);

    expect(screen.queryByText(/external model processing/i)).toBeNull();
    expect(screen.getByText(/model inference runs on this machine/i)).toBeVisible();
  });

  it("hides development fixtures outside fake mode", () => {
    render(<SetupView readiness={readyHostedMode} busy={false} onImport={vi.fn()} />);

    expect(screen.queryByRole("button", { name: /load development sample/i })).toBeNull();
  });

  it("never renders a credential input", () => {
    const { container } = render(
      <SetupView readiness={readyHostedMode} busy={false} onImport={vi.fn()} />,
    );

    expect(container.querySelector('input[type="password"]')).toBeNull();
    expect(screen.queryByLabelText(/api key/i)).toBeNull();
    expect(screen.queryByLabelText(/credential/i)).toBeNull();
  });
});
