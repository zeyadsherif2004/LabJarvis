import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ProtocolSummary, SessionSummary } from "../../types";
import { DashboardView } from "./DashboardView";

const protocol: ProtocolSummary = {
  protocol_id: "protocol-1",
  title: "Authoritative protocol",
  created_at: "2026-08-19T00:00:00Z",
  latest_version_id: "version-1",
  latest_version_status: "APPROVED",
  latest_revision: 1,
  step_count: 2,
  session_count: 1,
  archived_at: null,
};

const session: SessionSummary = {
  session_id: "session-1",
  protocol_id: "protocol-1",
  protocol_title: "Authoritative protocol",
  protocol_version_id: "version-1",
  state: "ACTIVE",
  version: 1,
  step_count: 2,
  completed_step_count: 0,
  active_step_number: 1,
  resumable: true,
  created_at: "2026-08-19T00:00:00Z",
  started_at: "2026-08-19T00:00:00Z",
  ended_at: null,
  label: null,
};

describe("DashboardView", () => {
  afterEach(() => vi.useRealTimers());

  it("keeps stale cards visible but prevents opening them during refresh", () => {
    render(
      <DashboardView
        protocols={[protocol]}
        sessions={[session]}
        loading={true}
        authoritative={false}
        error={null}
        onSearch={vi.fn()}
        onOpenProtocol={vi.fn()}
        onResumeSession={vi.fn()}
        onImport={vi.fn()}
        onRetry={vi.fn()}
      />,
    );

    expect(screen.getByText("Refreshing results...")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Resume/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Open/ })).toBeDisabled();
  });

  it("does not duplicate the hook's initial load and only searches after input", async () => {
    vi.useFakeTimers();
    const onSearch = vi.fn();
    render(
      <DashboardView
        protocols={[]}
        sessions={[]}
        loading={false}
        authoritative={true}
        error={null}
        onSearch={onSearch}
        onOpenProtocol={vi.fn()}
        onResumeSession={vi.fn()}
        onImport={vi.fn()}
        onRetry={vi.fn()}
      />,
    );

    await act(() => vi.advanceTimersByTime(251));
    expect(onSearch).not.toHaveBeenCalled();

    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "ethanol" } });
    await act(() => vi.advanceTimersByTime(251));
    expect(onSearch).toHaveBeenCalledOnce();
    expect(onSearch).toHaveBeenCalledWith({
      archived: false,
      createdFrom: "",
      createdTo: "",
      label: "",
      protocolVersionId: "",
      query: "ethanol",
      state: "",
    });
  });

  it("exposes keyboard-addressable lifecycle filters", () => {
    render(
      <DashboardView
        protocols={[]}
        sessions={[]}
        loading={false}
        authoritative={true}
        error={null}
        onSearch={vi.fn()}
        onOpenProtocol={vi.fn()}
        onResumeSession={vi.fn()}
        onImport={vi.fn()}
        onRetry={vi.fn()}
      />,
    );

    expect(screen.getByRole("group", { name: "Filter records" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Session state" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Session label" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Protocol version ID" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Show archived protocols" })).toBeInTheDocument();
  });
});
