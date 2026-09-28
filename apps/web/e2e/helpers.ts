import { expect, type Page } from "@playwright/test";

interface ActiveSession {
  sessionId: string;
  version: number;
}

/** Walk the dashboard -> import -> review -> approve -> start path. */
export async function startReviewedSession(page: Page): Promise<ActiveSession> {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Continue where you left off" })).toBeVisible();
  await page.getByRole("button", { name: "Import a protocol" }).click();
  await expect(page.getByRole("heading", { name: "System check" })).toBeVisible();
  await page.getByRole("button", { name: "Load development sample" }).click();
  await expect(page.getByRole("heading", { name: "Review imported protocol" })).toBeVisible();

  // Review, approval, and session creation now commit together, so there is one
  // response to wait for rather than three that could diverge.
  const createdResponse = page.waitForResponse(
    (response) =>
      response.url().includes("review-approve-and-start") &&
      response.request().method() === "POST" &&
      response.status() === 201,
  );
  await page.getByRole("button", { name: "Approve reviewed protocol" }).click();
  const created = (await (await createdResponse).json()) as { session: { id: string } };
  const sessionId = created.session.id;
  await expect(page.getByRole("heading", { name: "Protocol approved and ready" })).toBeVisible();

  const startedResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/v1/sessions/${sessionId}/start`) &&
      response.request().method() === "POST" &&
      response.status() === 200,
  );
  await page.getByRole("button", { name: "Start sterile session" }).click();
  const started = (await (await startedResponse).json()) as { version: number };
  await expect(
    page.getByRole("heading", { name: "Add 10 mL ethanol to the demonstration vessel." }),
  ).toBeVisible();
  return { sessionId, version: started.version };
}
