import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { fakeClient } from "../test/fixtures";
import type { ToolDescriptor } from "../types";
import { useToolRegistry } from "./useToolRegistry";

const completeStep: ToolDescriptor = {
  name: "complete_step",
  version: "1",
  kind: "mutating",
  confirmation: "model_proposals_only",
  summary: "Complete the active step.",
  legal_session_states: ["ACTIVE"],
  required_parameters: [],
  realtime_exposed: true,
};

describe("useToolRegistry", () => {
  it("loads the server registry once and indexes it by tool name", async () => {
    const getTools = vi.fn().mockResolvedValue({
      contract_version: "tools-v1",
      tools: [completeStep],
    });
    const client = fakeClient({ getTools });
    const { result, rerender } = renderHook(() => useToolRegistry(client));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.byName.get("complete_step")).toEqual(completeStep);
    expect(result.current.contractVersion).toBe("tools-v1");
    rerender();
    expect(getTools).toHaveBeenCalledOnce();
  });
});
