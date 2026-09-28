import { describe, expect, it, vi } from "vitest";

import { LabJarvisClient } from "./client";


describe("LabJarvisClient", () => {
  it("maps Problem Details without hiding the stable error code", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            type: "https://labjarvis.local/problems/session-not-found",
            title: "Session Not Found",
            status: 404,
            detail: "session was not found",
            code: "SESSION_NOT_FOUND",
            correlation_id: "correlation-1",
          }),
          { status: 404, headers: { "Content-Type": "application/problem+json" } },
        ),
      ),
    );
    const client = new LabJarvisClient("");

    await expect(client.getSession("missing")).rejects.toMatchObject({
      problem: { code: "SESSION_NOT_FOUND", correlation_id: "correlation-1" },
    });
  });

  it("sends optimistic version and idempotency headers for commands", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          transcript: "pause",
          intent: "PAUSE_SESSION",
          confidence: 0.99,
          ambiguities: [],
          pending_action: null,
          response_text: "Proposed session pause. Review and confirm before it is applied.",
          replayed: false,
        }),
        { status: 202, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const client = new LabJarvisClient("");

    await client.submitTextCommand("session-1", "pause", 3, "command-1");

    const call = fetchMock.mock.calls[0];
    expect(call?.[0]).toBe("/api/v1/sessions/session-1/commands/text");
    const headers = new Headers(call?.[1]?.headers);
    expect(headers.get("If-Match")).toBe('"3"');
    expect(headers.get("Idempotency-Key")).toBe("command-1");
  });

  it("sends optimistic version and idempotency headers for reagent recognition", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ id: "verification-1" }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const client = new LabJarvisClient("");

    await client.verifyReagent(
      "session-1",
      new Blob(["label"], { type: "image/jpeg" }),
      "requirement-1",
      4,
      "reagent-read-1",
    );

    const headers = new Headers(fetchMock.mock.calls[0]?.[1]?.headers);
    expect(headers.get("If-Match")).toBe('"4"');
    expect(headers.get("Idempotency-Key")).toBe("reagent-read-1");
  });

  it("sends optimistic version and idempotency headers for reagent decisions", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ id: "verification-1" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const client = new LabJarvisClient("");

    await client.confirmReagentVerification(
      "verification-1",
      true,
      null,
      5,
      "reagent-decision-1",
    );

    const headers = new Headers(fetchMock.mock.calls[0]?.[1]?.headers);
    expect(headers.get("If-Match")).toBe('"5"');
    expect(headers.get("Idempotency-Key")).toBe("reagent-decision-1");
  });

  it("encodes discovery filters for lifecycle management", async () => {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({ protocols: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ sessions: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }));
    vi.stubGlobal("fetch", fetchMock);
    const client = new LabJarvisClient("");

    await client.listProtocols({ query: "ethanol", archived: true, createdFrom: "2026-08-01" });
    await client.listSessions({ label: "batch-a", state: "ACTIVE", protocolArchived: false });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/v1/protocols?q=ethanol&archived=true&from=2026-08-01",
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/v1/sessions?label=batch-a&state=ACTIVE&protocol_archived=false",
    );
  });

  it("sends the typed deletion phrase in the aggregate delete body", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ deletion_id: "deletion-1" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const client = new LabJarvisClient("");

    await client.deleteSession("session-1", "DELETE session-1");

    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/data/sessions/session-1");
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      method: "DELETE",
      body: JSON.stringify({ confirmation: "DELETE session-1" }),
    });
  });

  it("requests bounded OCR only when the operator selects it", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ protocol_id: "protocol-1" }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const client = new LabJarvisClient("");

    await client.uploadProtocol(new File(["%PDF"], "scan.pdf"), true);

    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/protocols/upload?ocr=true");
  });
});
