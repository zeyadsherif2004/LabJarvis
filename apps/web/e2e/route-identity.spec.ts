import { expect, test } from "@playwright/test";

import { startReviewedSession } from "./helpers";

/**
 * The audited failure, reproduced in a real browser.
 *
 * Navigating to a session that does not exist used to leave the previous
 * session rendered, with working controls, under a URL naming another one. The
 * operator saw one experiment while every button acted on a different one.
 */

test("a nonexistent session route never shows the previous session", async ({ page }) => {
  const { sessionId } = await startReviewedSession(page);
  await expect(
    page.getByRole("heading", { name: "Add 10 mL ethanol to the demonstration vessel." }),
  ).toBeVisible();

  await page.evaluate(() => {
    globalThis.location.hash = "#/sessions/not-a-real-session";
  });

  await expect(page.getByRole("heading", { name: "That session does not exist" })).toBeVisible();
  // The previous experiment is gone, and so are the controls that acted on it.
  await expect(
    page.getByRole("heading", { name: "Add 10 mL ethanol to the demonstration vessel." }),
  ).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Complete current step" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "End session early" })).toHaveCount(0);

  // Going back to the real session still works, so this is a guard rather than
  // a dead end.
  await page.evaluate((id) => {
    globalThis.location.hash = `#/sessions/${id}`;
  }, sessionId);
  await expect(
    page.getByRole("heading", { name: "Add 10 mL ethanol to the demonstration vessel." }),
  ).toBeVisible();
});

test("a nonexistent protocol route never shows the previous protocol", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Import a protocol" }).click();
  await page.getByRole("button", { name: "Load development sample" }).click();
  await expect(page.getByRole("heading", { name: "Review imported protocol" })).toBeVisible();

  await page.evaluate(() => {
    globalThis.location.hash = "#/protocols/not-a-real-protocol";
  });

  await expect(page.getByRole("heading", { name: "That protocol does not exist" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Review imported protocol" })).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Approve reviewed protocol" }),
  ).toHaveCount(0);
});

test("protocol review shows the whole extracted source, not one excerpt", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Import a protocol" }).click();
  await page.getByRole("button", { name: "Load development sample" }).click();
  await expect(page.getByRole("heading", { name: "Review imported protocol" })).toBeVisible();

  const document = page.getByRole("region", { name: "Source document" });
  await expect(document).toBeVisible();
  await expect(document).toContainText("characters extracted");
  // The reviewer is told plainly what this view can and cannot show them.
  await expect(document).toContainText("uploaded file itself is not kept");
});

test("an edited draft is saved without the operator asking", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Import a protocol" }).click();
  await page.getByRole("button", { name: "Load development sample" }).click();
  await expect(page.getByRole("heading", { name: "Review imported protocol" })).toBeVisible();

  const saved = page.waitForResponse(
    (response) =>
      response.url().includes("/api/v1/protocol-versions/") &&
      response.request().method() === "PUT" &&
      response.status() === 200,
  );
  const instruction = page.getByLabel("Reviewed instruction").first();
  await instruction.fill("Add 10 mL ethanol to the demonstration vessel, checked.");
  await saved;

  await expect(page.getByText(/Saved as revision/)).toBeVisible();

  // A reload brings the review back, which is the point of saving it.
  await page.reload();
  await expect(page.getByLabel("Reviewed instruction").first()).toHaveValue(
    "Add 10 mL ethanol to the demonstration vessel, checked.",
  );
});
