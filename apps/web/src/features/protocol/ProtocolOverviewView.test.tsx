import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { approvedProtocol } from "../../test/fixtures";
import { ProtocolOverviewView } from "./ProtocolOverviewView";

describe("ProtocolOverviewView lifecycle", () => {
  it("copies an approved version into a draft without mutating the source", async () => {
    const user = userEvent.setup();
    const onCopy = vi.fn().mockResolvedValue(undefined);
    render(
      <ProtocolOverviewView
        protocol={approvedProtocol}
        archivedAt={null}
        sessionSummary={null}
        onOpenSession={vi.fn()}
        onCopy={onCopy}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Copy as new draft" }));
    expect(onCopy).toHaveBeenCalledOnce();
    expect(screen.getByText(/approved version remains read-only/i)).toBeInTheDocument();
  });

  it("switches between archive and restore controls", async () => {
    const user = userEvent.setup();
    const onArchive = vi.fn().mockResolvedValue("2026-08-19T12:00:00Z");
    const onRestore = vi.fn().mockResolvedValue(null);
    render(
      <ProtocolOverviewView
        protocol={approvedProtocol}
        archivedAt={null}
        sessionSummary={null}
        onOpenSession={vi.fn()}
        onCopy={vi.fn()}
        onArchive={onArchive}
        onRestore={onRestore}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Archive protocol" }));
    expect(await screen.findByRole("button", { name: "Restore protocol" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Restore protocol" }));
    expect(await screen.findByRole("button", { name: "Archive protocol" })).toBeInTheDocument();
  });
});
