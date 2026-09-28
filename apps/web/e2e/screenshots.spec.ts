import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));
const screenshotDirectory = path.resolve(here, "../../../docs/screenshots");

async function capture(page: Page, name: string, fullPage = true): Promise<void> {
  await page.evaluate(async () => document.fonts.ready);
  await page.screenshot({
    path: path.join(screenshotDirectory, name),
    fullPage,
    animations: "disabled",
  });
}

test.beforeAll(async () => {
  await mkdir(screenshotDirectory, { recursive: true });
});

test("captures the actual deterministic operator journey", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "LabJarvis" })).toBeVisible();
  await page.getByRole("button", { name: "Import a protocol" }).click();
  await expect(page.getByRole("heading", { name: "System check" })).toBeVisible();
  await capture(page, "01-system-check.png");

  await page.getByRole("button", { name: "Load development sample" }).click();
  await expect(page.getByRole("heading", { name: "Review imported protocol" })).toBeVisible();
  await page.getByLabel("Step title").first().focus();
  await page.evaluate(() => window.scrollTo(0, 0));
  await capture(page, "02-protocol-review.png", false);

  const createdResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith("/review-approve-and-start") &&
      response.request().method() === "POST" &&
      response.status() === 201,
  );
  await page.getByRole("button", { name: "Approve reviewed protocol" }).click();
  const created = (await (await createdResponse).json()) as { session: { id: string } };
  await expect(page.getByRole("heading", { name: "Protocol approved and ready" })).toBeVisible();

  const startedResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/v1/sessions/${created.session.id}/start`) &&
      response.request().method() === "POST" &&
      response.status() === 200,
  );
  await page.getByRole("button", { name: "Start sterile session" }).click();
  await startedResponse;
  await expect(
    page.getByRole("heading", { name: "Add 10 mL ethanol to the demonstration vessel." }),
  ).toBeVisible();
  await capture(page, "03-sterile-mode.png");

  await page.getByLabel("Typed command").fill("Record the temperature as 37 degrees Celsius");
  await page.getByRole("button", { name: "Submit command" }).click();
  const confirmation = page.getByRole("dialog", { name: "Confirm before recording" });
  await expect(confirmation).toContainText("37 degC");
  await confirmation.getByRole("button", { name: "Confirm action" }).click();
  await expect(
    page.getByRole("region", { name: "Notebook activity (append-only)" }),
  ).toContainText("temperature 37 degC");

  const verification = page.getByRole("region", { name: "Reagent verification" });
  await verification
    .getByRole("combobox", { name: "Development label fixture" })
    .selectOption("ethanol");
  await verification.getByRole("button", { name: "Verify visible label" }).click();
  await expect(verification.getByRole("status")).toContainText("MATCH");
  await capture(page, "04-reagent-match-review.png");
  await verification.getByRole("button", { name: "Confirm visible reading" }).click();
  await expect(verification.getByRole("status")).toContainText("CONFIRMED");

  await verification
    .getByRole("combobox", { name: "Development label fixture" })
    .selectOption("methanol");
  await verification.getByRole("button", { name: "Verify visible label" }).click();
  await expect(verification.getByRole("alert")).toContainText("MISMATCH");
  await capture(page, "05-reagent-mismatch-warning.png");
  await verification.getByRole("button", { name: "Reject OCR proposal" }).click();

  await page.getByRole("button", { name: "Notebook" }).click();
  await expect(page.getByRole("heading", { name: "Session notebook" })).toBeVisible();
  await capture(page, "06-session-notebook.png");
});
