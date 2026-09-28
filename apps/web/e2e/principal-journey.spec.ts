import { expect, test } from "@playwright/test";

import { startReviewedSession } from "./helpers";


test("completes the reviewed protocol workflow", async ({ page }) => {
  await startReviewedSession(page);

  await page.getByLabel("Typed command").fill("Record the temperature as 37 degrees Celsius");
  await page.getByRole("button", { name: "Submit command" }).click();
  const confirmation = page.getByRole("dialog", { name: "Confirm before recording" });
  await expect(confirmation).toContainText("37 degC");
  await confirmation.getByRole("button", { name: "Confirm action" }).click();

  const activity = page.getByRole("region", { name: "Notebook activity (append-only)" });
  await expect(activity).toContainText("temperature 37 degC");
  const verification = page.getByRole("region", { name: "Reagent verification" });
  await verification.getByRole("combobox", { name: "Development label fixture" }).selectOption("ethanol");
  await verification.getByRole("button", { name: "Verify visible label" }).click();
  await expect(verification.getByRole("status")).toContainText("MATCH");
  await verification.getByRole("button", { name: "Confirm visible reading" }).click();
  await expect(verification.getByRole("status")).toContainText("CONFIRMED");

  await page.getByRole("button", { name: "Notebook" }).click();
  await expect(page.getByRole("heading", { name: "Session notebook" })).toBeVisible();
  await page.getByRole("button", { name: "Correct measurement entry" }).click();
  const correction = "Temperature confirmed as 37.2 degrees Celsius after calibration.";
  await page.getByLabel("Append correction").fill(correction);
  await page.getByRole("button", { name: "Append correction" }).click();
  await expect(page.getByText(correction, { exact: true })).toBeVisible();

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("link", { name: "JSON" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.json$/);
});
