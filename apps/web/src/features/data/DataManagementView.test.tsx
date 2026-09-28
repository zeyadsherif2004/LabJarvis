import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { fakeClient } from "../../test/fixtures";
import { readyFakeMode } from "../../test/fixtures";
import { DataManagementView } from "./DataManagementView";

describe("DataManagementView", () => {
  it("puts export before deletion and requires the exact session confirmation", async () => {
    const user = userEvent.setup();
    const deleteSession = vi.fn().mockResolvedValue({
      deletion_id: "deletion-1",
      deleted_at: "2026-08-19T12:00:00Z",
      object_type: "session",
      object_id_hash: "a".repeat(64),
      cleanup_state: "complete",
    });
    const client = fakeClient({ deleteSession });

    const { container } = render(
      <DataManagementView
        client={client}
        readiness={readyFakeMode}
        sessionId="session-1"
        onSessionDeleted={vi.fn()}
      />,
    );

    const exportHeading = screen.getByRole("heading", { name: "Export session data" });
    const deletionHeading = screen.getByRole("heading", { name: "Delete session" });
    expect(
      exportHeading.compareDocumentPosition(deletionHeading) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(container.querySelector('a[href="#notebook-export"]')).not.toBeNull();

    await user.click(screen.getByRole("button", { name: "Delete session" }));
    const confirmation = screen.getByRole("textbox", {
      name: "Type DELETE session-1 to confirm",
    });
    expect(confirmation).toHaveFocus();
    const confirm = screen.getByRole("button", { name: "Permanently delete session" });
    expect(confirm).toBeDisabled();

    await user.type(confirmation, "DELETE session-01");
    expect(confirm).toBeDisabled();
    await user.clear(confirmation);
    await user.type(confirmation, "DELETE session-1");
    expect(confirm).toBeEnabled();
    await user.click(confirm);

    expect(deleteSession).toHaveBeenCalledWith("session-1", "DELETE session-1");
  });

  it("restores focus after escape and preserves confirmation text after failure", async () => {
    const user = userEvent.setup();
    const client = fakeClient({
      deleteSession: vi.fn().mockRejectedValue(new Error("Deletion refused")),
    });
    render(
      <DataManagementView
        client={client}
        readiness={readyFakeMode}
        sessionId="session-1"
        onSessionDeleted={vi.fn()}
      />,
    );

    const trigger = screen.getByRole("button", { name: "Delete session" });
    await user.click(trigger);
    await user.keyboard("{Escape}");
    expect(trigger).toHaveFocus();

    await user.click(trigger);
    const confirmation = screen.getByRole("textbox", {
      name: "Type DELETE session-1 to confirm",
    });
    await user.type(confirmation, "DELETE session-1");
    await user.click(screen.getByRole("button", { name: "Permanently delete session" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Deletion refused");
    expect(confirmation).toHaveValue("DELETE session-1");
  });

  it("creates a backup and validates it into an inactive restore staging area", async () => {
    const user = userEvent.setup();
    const manifest = {
      format_version: "1",
      created_at: "2026-08-19T12:00:00Z",
      schema_revision: "0009_session_labels_archive",
      application_commit: null,
      profile: "sqlite",
      files: [{ path: "database.sqlite3", size: 42, sha256: "b".repeat(64) }],
    };
    const createBackup = vi.fn().mockResolvedValue({ backup_name: "backup-1", manifest });
    const validateBackup = vi.fn().mockResolvedValue({
      backup_name: "backup-1",
      staging_name: "restore-1",
      manifest,
      activation_required: true,
    });
    render(
      <DataManagementView
        client={fakeClient({ createBackup, validateBackup })}
        readiness={readyFakeMode}
        sessionId={null}
        onSessionDeleted={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Create verified backup" }));
    expect(await screen.findByText(/backup-1/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Validate and stage restore" }));
    expect(await screen.findByText(/restore-1/)).toBeInTheDocument();
    expect(screen.getByText(/not activated automatically/i)).toBeInTheDocument();
  });
});
