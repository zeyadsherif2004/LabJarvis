import { expect, test } from "@playwright/test";

import { startReviewedSession } from "./helpers";

/**
 * The properties an interrupted bench session actually depends on: the work can
 * be found again, a reload restores it exactly, a timer keeps running across
 * that reload, and controls reach the database without a language model.
 */

test("an active session survives a full browser reload", async ({ page }) => {
  const { sessionId } = await startReviewedSession(page);

  // A reload discards every piece of in-memory state.
  await page.reload();

  await expect(
    page.getByRole("heading", { name: "Add 10 mL ethanol to the demonstration vessel." }),
  ).toBeVisible();
  expect(page.url()).toContain(`#/sessions/${sessionId}`);
});

test("a timer keeps counting across a reload", async ({ page }) => {
  await startReviewedSession(page);

  await page.getByLabel("Start a timer (seconds)").fill("600");
  await page.getByRole("button", { name: "Start timer" }).click();
  const timers = page.getByRole("region", { name: "Timers" });
  await expect(timers).toContainText(/\d\d:\d\d/);

  await page.reload();

  // The remaining time is recomputed from the stored deadline, not from a
  // counter that the reload destroyed.
  await expect(page.getByRole("region", { name: "Timers" })).toContainText(/0\d:\d\d/);
});

test("a timer can be paused and resumed without losing its remainder", async ({ page }) => {
  await startReviewedSession(page);
  await page.getByLabel("Start a timer (seconds)").fill("300");
  await page.getByRole("button", { name: "Start timer" }).click();
  const timers = page.getByRole("region", { name: "Timers" });

  await timers.getByRole("button", { name: /^Pause/ }).click();
  await expect(timers).toContainText("(paused)");
  await timers.getByRole("button", { name: /^Resume/ }).click();

  await expect(timers).not.toContainText("(paused)");
});

test("the dashboard lists the session so it can be resumed without a bookmark", async ({
  page,
}) => {
  const { sessionId } = await startReviewedSession(page);

  await page.getByRole("button", { name: "Home", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Continue where you left off" })).toBeVisible();
  await page.getByRole("button", { name: /Resume/ }).first().click();

  await expect(
    page.getByRole("heading", { name: "Add 10 mL ethanol to the demonstration vessel." }),
  ).toBeVisible();
  expect(page.url()).toContain(sessionId);
});

test("pausing a session does not invoke the language model", async ({ page }) => {
  await startReviewedSession(page);
  const textCommands: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/commands/text")) textCommands.push(request.url());
  });

  await page.getByRole("button", { name: "Pause session" }).click();

  await expect(page.getByText("Session paused")).toBeVisible();
  // A button already states its intent; sending it through text understanding
  // would add latency and a failure mode without adding safety.
  expect(textCommands).toEqual([]);
});

test("skipping a step requires a reason and records it", async ({ page }) => {
  await startReviewedSession(page);

  await page.getByRole("button", { name: "Skip step with a reason" }).click();
  await page
    .getByLabel("Why is this step being skipped? The reason is recorded permanently.")
    .fill("Reagent unavailable at the bench");
  await page.getByRole("button", { name: "Skip step and record the reason" }).click();

  // Skipping writes to the experimental record, so it is always confirmed.
  const confirmation = page.getByRole("dialog", { name: "Confirm before recording" });
  await expect(confirmation).toContainText("Reagent unavailable at the bench");
  await confirmation.getByRole("button", { name: "Confirm action" }).click();

  const activity = page.getByRole("region", { name: "Notebook activity (append-only)" });
  await expect(activity).toContainText("Reagent unavailable at the bench");
});

test("ending a session early produces an honest terminal summary", async ({ page }) => {
  await startReviewedSession(page);

  await page.getByRole("button", { name: "End session early" }).click();
  const confirmation = page.getByRole("dialog", { name: "Confirm before recording" });
  await confirmation.getByRole("button", { name: "Confirm action" }).click();

  // Ending a session takes the operator to the record of what actually happened.
  await expect(page.getByRole("heading", { name: "Session notebook" })).toBeVisible();
  await expect(page.getByText(/ended early/i)).toBeVisible();
  await expect(page.getByText(/never performed/i)).toBeVisible();
});
