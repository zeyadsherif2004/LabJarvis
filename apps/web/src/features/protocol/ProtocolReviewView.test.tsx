import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiProblem } from "../../api/client";
import { successfulAction, type ActionResult } from "../../actions";
import { fakeClient } from "../../test/fixtures";
import { ProtocolReviewView } from "./ProtocolReviewView";
import type { ProblemDetail, ProtocolStep, ProtocolVersion } from "../../types";


const version: ProtocolVersion = {
  id: "version-1",
  protocol_id: "protocol-1",
  version_number: 1,
  revision: 0,
  status: "DRAFT",
  source_sha256: "a".repeat(64),
  created_at: "2026-07-16T12:00:00Z",
  approved_at: null,
  steps: [
    {
      id: "step-1",
      number: 1,
      title: "Add ethanol",
      instruction: "Add 10 mL ethanol.",
      requirements: [{
        requirement_id: "step-1-req-1",
        name: "ethanol",
        quantity: "10",
        unit: "mL",
        aliases: [],
      }],
      duration_seconds: null,
      safety_notes: [],
      source: { page: 1, section: "Preparation", excerpt: "1. Add 10 mL ethanol." },
      parallel_allowed: false,
    },
    {
      id: "step-2",
      number: 2,
      title: "Incubate",
      instruction: "Incubate for 5 minutes.",
      requirements: [],
      duration_seconds: 300,
      safety_notes: [],
      source: { page: 1, section: "Preparation", excerpt: "2. Incubate." },
      parallel_allowed: false,
    },
  ],
};


type ApproveFn = (
  steps: ProtocolVersion["steps"],
  revision: number,
  label: string | null,
) => Promise<ActionResult>;

function problem(code: string): ApiProblem {
  const detail: ProblemDetail = {
    type: "about:blank",
    title: code,
    status: 409,
    detail: "changed elsewhere",
    code,
    correlation_id: "corr-1",
  };
  return new ApiProblem(detail);
}

describe("ProtocolReviewView", () => {
  it("submits the operator-reviewed order with contiguous numbering", async () => {
    const user = userEvent.setup();
    const approve = vi.fn<ApproveFn>().mockResolvedValue(successfulAction);
    const reviewProtocol = vi.fn().mockResolvedValue({ ...version, revision: 1 });
    render(
      <ProtocolReviewView
        client={fakeClient({ reviewProtocol })}
        version={version}
        onApprove={approve}
        busy={false}
      />,
    );

    await user.click(screen.getByRole("button", { name: /move step 1 down/i }));
    await user.click(screen.getByRole("button", { name: /approve reviewed protocol/i }));

    await waitFor(() => expect(approve).toHaveBeenCalledTimes(1));
    const submitted = approve.mock.calls[0]?.[0];
    expect(submitted?.map((step) => [step.id, step.number])).toEqual([
      ["step-2", 1],
      ["step-1", 2],
    ]);
  });

  it("passes the optional run label without collecting participant identifiers", async () => {
    const user = userEvent.setup();
    const approve = vi.fn<ApproveFn>().mockResolvedValue(successfulAction);
    render(
      <ProtocolReviewView
        client={fakeClient({ reviewProtocol: vi.fn().mockResolvedValue(version) })}
        version={version}
        onApprove={approve}
        busy={false}
      />,
    );

    await user.type(screen.getByRole("textbox", { name: /run label/i }), "batch 7, replicate A");
    await user.click(screen.getByRole("button", { name: /approve reviewed protocol/i }));

    await waitFor(() => expect(approve).toHaveBeenCalledTimes(1));
    expect(approve.mock.calls[0]?.[2]).toBe("batch 7, replicate A");
    expect(screen.getByText(/do not enter participant, patient, or personal identifiers/i)).toBeVisible();
  });

  it("lets the operator add and remove steps before approval", async () => {
    const user = userEvent.setup();
    const approve = vi.fn<ApproveFn>().mockResolvedValue(successfulAction);
    render(
      <ProtocolReviewView
        client={fakeClient({ reviewProtocol: vi.fn().mockResolvedValue(version) })}
        version={version}
        onApprove={approve}
        busy={false}
      />,
    );

    await user.click(screen.getByRole("button", { name: /add reviewed step/i }));
    expect(screen.getAllByLabelText("Step title")).toHaveLength(3);
    await user.click(screen.getByRole("button", { name: /remove step 3/i }));
    expect(screen.getAllByLabelText("Step title")).toHaveLength(2);
  });

  it("saves an in-progress review so a reload does not lose it", async () => {
    const user = userEvent.setup();
    const reviewProtocol = vi.fn().mockResolvedValue({ ...version, revision: 1 });
    render(
      <ProtocolReviewView
        client={fakeClient({ reviewProtocol })}
        version={version}
        onApprove={vi.fn<ApproveFn>().mockResolvedValue(successfulAction)}
        busy={false}
      />,
    );

    await user.click(screen.getByRole("button", { name: /move step 1 down/i }));

    expect(await screen.findByText(/unsaved changes/i)).toBeVisible();
    await waitFor(() => expect(reviewProtocol).toHaveBeenCalled(), { timeout: 4000 });
    expect(await screen.findByText(/saved as revision 1/i)).toBeVisible();
    // Saved against the draft on screen, at the revision it was loaded at.
    const [savedVersionId, , savedRevision] = reviewProtocol.mock.calls[0] as [
      string,
      unknown,
      number,
    ];
    expect(savedVersionId).toBe("version-1");
    expect(savedRevision).toBe(0);
  });

  it("stops approval and explains when the draft changed elsewhere", async () => {
    const user = userEvent.setup();
    const reviewProtocol = vi.fn().mockRejectedValue(problem("PROTOCOL_VERSION_CONFLICT"));
    const approve = vi.fn<ApproveFn>().mockResolvedValue(successfulAction);
    render(
      <ProtocolReviewView
        client={fakeClient({ reviewProtocol })}
        version={version}
        onApprove={approve}
        busy={false}
      />,
    );

    await user.click(screen.getByRole("button", { name: /move step 1 down/i }));

    expect(
      await screen.findByText(/changed somewhere else/i, {}, { timeout: 4000 }),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: /approve reviewed protocol/i })).toBeDisabled();
    expect(approve).not.toHaveBeenCalled();
  });

  it("shows the whole extracted source, not only an excerpt", async () => {
    const getProtocolSource = vi.fn().mockResolvedValue({
      version_id: "version-1",
      source_sha256: "b".repeat(64),
      text: [
        "Ethanol precipitation protocol",
        "Store ethanol below four degrees Celsius.",
        "1. Add 10 mL ethanol.",
        "2. Incubate.",
      ].join("\n"),
      character_count: 104,
    });
    render(
      <ProtocolReviewView
        client={fakeClient({ getProtocolSource })}
        version={version}
        onApprove={vi.fn<ApproveFn>().mockResolvedValue(successfulAction)}
        busy={false}
      />,
    );

    expect(await screen.findByRole("heading", { name: /source document/i })).toBeVisible();
    // Material the reviewer left out of the steps is still visible, which is
    // the whole reason an excerpt was not enough.
    expect(
      screen.getByText("Store ethanol below four degrees Celsius."),
    ).toBeVisible();
    expect(screen.getByText(/uploaded file itself is not kept/i)).toBeVisible();
    expect(getProtocolSource).toHaveBeenCalledWith("version-1");
  });

  it("blocks approval until every import flag has been checked", async () => {
    const user = userEvent.setup();
    const approve = vi.fn<ApproveFn>().mockResolvedValue(successfulAction);
    const [first, second] = version.steps as [ProtocolStep, ProtocolStep];
    const flagged: ProtocolVersion = {
      ...version,
      steps: [
        {
          ...first,
          requirements: [{ ...first.requirements[0]!, quantity: null, unit: null }],
          flags: [
            {
              code: "QUANTITY_RANGE",
              message: "The source gives a range ('1-2 mL'), so no quantity was set.",
              excerpt: "1-2 mL ethanol",
              acknowledged: false,
            },
          ],
        },
        second,
      ],
    };
    render(
      <ProtocolReviewView
        client={fakeClient({ reviewProtocol: vi.fn().mockResolvedValue({ ...flagged, revision: 1 }) })}
        version={flagged}
        onApprove={approve}
        busy={false}
      />,
    );

    const flags = screen.getByRole("region", { name: /import flags for step 1/i });
    expect(within(flags).getByText("Amount given as a range")).toBeVisible();
    expect(within(flags).getByText("1-2 mL ethanol")).toBeVisible();
    expect(screen.getByText(/1 item was flagged during import/i)).toBeVisible();
    const approveButton = screen.getByRole("button", { name: /approve reviewed protocol/i });
    expect(approveButton).toBeDisabled();
    expect(screen.getByText("Step 1: check 1 import flag")).toBeVisible();

    await user.click(within(flags).getByRole("checkbox", { name: /checked this against the source/i }));

    expect(approveButton).toBeEnabled();
    await user.click(approveButton);
    await waitFor(() => expect(approve).toHaveBeenCalledTimes(1));
    expect(approve.mock.calls[0]?.[0][0]?.flags?.[0]?.acknowledged).toBe(true);
  });

  it("will not approve a step that still holds placeholder text", async () => {
    const user = userEvent.setup();
    const approve = vi.fn<ApproveFn>().mockResolvedValue(successfulAction);
    render(
      <ProtocolReviewView
        client={fakeClient({ reviewProtocol: vi.fn().mockResolvedValue({ ...version, revision: 1 }) })}
        version={version}
        onApprove={approve}
        busy={false}
      />,
    );

    await user.click(screen.getByRole("button", { name: /add reviewed step/i }));

    const approveButton = screen.getByRole("button", { name: /approve reviewed protocol/i });
    expect(approveButton).toBeDisabled();
    expect(screen.getByText("Step 3: replace the placeholder title")).toBeVisible();
    expect(screen.getByText("Step 3: write the reviewed instruction")).toBeVisible();

    const titles = screen.getAllByLabelText("Step title");
    await user.clear(titles[2]!);
    await user.type(titles[2]!, "Seal the tube");
    const instructions = screen.getAllByLabelText("Reviewed instruction");
    await user.clear(instructions[2]!);
    await user.type(instructions[2]!, "Close the cap firmly.");

    expect(approveButton).toBeEnabled();
    await user.click(screen.getAllByRole("button", { name: /add requirement/i })[2]!);
    expect(approveButton).toBeDisabled();
    expect(
      screen.getByText("Step 3: name or remove the placeholder requirement"),
    ).toBeVisible();
    expect(approve).not.toHaveBeenCalled();
  });

  it("lists source text that was not used as steps and can locate it", async () => {
    const user = userEvent.setup();
    const getProtocolSource = vi.fn().mockResolvedValue({
      version_id: "version-1",
      source_sha256: "b".repeat(64),
      text: ["Materials", "- Ethanol 70%", "- Tris buffer", "Procedure", "1. Add 10 mL ethanol."].join(
        "\n",
      ),
      character_count: 70,
      unassigned_text: [
        {
          page: 1,
          section: "Materials",
          text: "- Ethanol 70%\n- Tris buffer",
          reason: "NON_PROCEDURE_SECTION",
        },
      ],
    });
    render(
      <ProtocolReviewView
        client={fakeClient({ getProtocolSource })}
        version={version}
        onApprove={vi.fn<ApproveFn>().mockResolvedValue(successfulAction)}
        busy={false}
      />,
    );

    const panel = await screen.findByRole("region", { name: /source text not used as steps/i });
    expect(within(panel).getByText(/not part of the procedure/i)).toBeVisible();
    expect(within(panel).getByText("Page 1 - Materials")).toBeVisible();

    await user.click(within(panel).getByRole("button", { name: /find this in the source/i }));

    const source = screen.getByRole("region", { name: /source document/i });
    const highlighted = source.querySelectorAll(".source-document-line--match");
    expect(Array.from(highlighted).map((line) => line.textContent)).toEqual([
      "Matches the selected item: - Ethanol 70%",
      "- Tris buffer",
    ]);
  });
});
