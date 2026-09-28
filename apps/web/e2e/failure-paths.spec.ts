import { expect, test } from "@playwright/test";

import { startReviewedSession } from "./helpers";


test("rejects a malformed protocol and leaves import recovery available", async ({ page }) => {
  await page.goto("/#/import");
  await expect(page.getByRole("heading", { name: "System check" })).toBeVisible();
  await page.getByLabel("Protocol document").setInputFiles({
    name: "malformed.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4\nthis is not a valid document"),
  });

  await expect(page.getByRole("alert")).toContainText(/PDF could not be parsed/i);
  await expect(page.getByRole("button", { name: "Choose protocol document" })).toBeEnabled();
});

test("shows low-confidence recovery and microphone permission fallback", async ({ page }) => {
  await startReviewedSession(page);
  const activeStep = page.getByRole("region", {
    name: "Add 10 mL ethanol to the demonstration vessel.",
  });
  await activeStep.getByLabel("Typed command").fill("make it so");
  await activeStep.getByRole("button", { name: "Submit command" }).click();
  // The step also contains an intentionally live <output> for command results,
  // which has an implicit status role. Target the operator feedback message
  // explicitly so this assertion does not become ambiguous as either region evolves.
  await expect(activeStep.locator(".operator-feedback")).toContainText("unsupported command");

  await activeStep.getByRole("button", { name: /Hold to speak/ }).click();
  await expect(activeStep.getByRole("alert")).toContainText(
    /Microphone (permission denied|capture is unavailable)/i,
  );
  await expect(activeStep.getByLabel("Typed command")).toBeEnabled();
});

test("surfaces a visible reagent mismatch without relying on colour", async ({ page }) => {
  await startReviewedSession(page);
  const verification = page.getByRole("region", { name: "Reagent verification" });
  await verification.getByRole("combobox", { name: "Development label fixture" }).selectOption("methanol");
  await verification.getByRole("button", { name: "Verify visible label" }).click();

  const warning = verification.getByRole("alert");
  await expect(warning).toContainText("MISMATCH");
  await expect(warning).toContainText("Do not proceed");
  await expect(warning).toContainText("Expected ethanol; observed methanol");
});

test("contains fake-model failures and prohibited transitions at the API boundary", async ({ page }) => {
  const session = await startReviewedSession(page);
  const badAudio = await page.request.post(
    `/api/v1/sessions/${session.sessionId}/commands/audio`,
    {
      headers: {
        "If-Match": `"${session.version}"`,
        "Idempotency-Key": "e2e-invalid-audio",
      },
      multipart: {
        file: {
          name: "invalid.webm",
          mimeType: "audio/webm",
          buffer: Buffer.from("not-a-fake-fixture"),
        },
      },
    },
  );
  expect(badAudio.status()).toBe(422);
  await expect(badAudio.json()).resolves.toMatchObject({ code: "MODEL_INPUT_INVALID" });

  const repeatedStart = await page.request.post(
    `/api/v1/sessions/${session.sessionId}/start`,
    { headers: { "If-Match": `"${session.version}"` } },
  );
  expect(repeatedStart.status()).toBe(422);
  await expect(repeatedStart.json()).resolves.toMatchObject({
    code: "INVALID_SESSION_TRANSITION",
  });
  await expect(page.getByRole("button", { name: "Submit command" })).toBeEnabled();
});
