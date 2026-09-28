import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { startReviewedSession } from "./helpers";


async function expectNoSeriousAccessibilityViolations(page: Page): Promise<void> {
  const scan = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  const serious = scan.violations.filter(
    (violation) => violation.impact === "serious" || violation.impact === "critical",
  );
  expect(serious).toEqual([]);
}

test("the dashboard has no serious automated accessibility violations", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Continue where you left off" }),
  ).toBeVisible();
  await expectNoSeriousAccessibilityViolations(page);
});

test("setup has no serious automated accessibility violations", async ({ page }) => {
  await page.goto("/#/import");
  await expect(page.getByRole("heading", { name: "System check" })).toBeVisible();
  await expectNoSeriousAccessibilityViolations(page);
});

test("the diagnostics page has no serious automated accessibility violations", async ({
  page,
}) => {
  await page.goto("/#/diagnostics");
  await expect(page.getByRole("heading", { name: "Model readiness" })).toBeVisible();
  await expectNoSeriousAccessibilityViolations(page);
});

test("data management has no serious automated accessibility violations", async ({ page }) => {
  await page.goto("/#/data");
  await expect(page.getByRole("heading", { name: "Data management" })).toBeVisible();
  await expectNoSeriousAccessibilityViolations(page);
});

test("a narrow mobile viewport keeps the session usable", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 740 });
  await startReviewedSession(page);
  await expect(page.getByRole("button", { name: "Submit command" })).toBeVisible();
  await expectNoSeriousAccessibilityViolations(page);
});

test("sterile mode has no serious automated accessibility violations", async ({ page }) => {
  await startReviewedSession(page);
  await expectNoSeriousAccessibilityViolations(page);
});

test("sterile-mode controls meet the 44 px gloved-touch target size", async ({ page }) => {
  await startReviewedSession(page);
  const undersized = await page.evaluate(() => {
    const controls = Array.from(
      document.querySelectorAll<HTMLElement>(
        ".sterile-mode button, .sterile-mode select, .sterile-mode input:not([type=radio]):not([type=checkbox]):not([type=file])",
      ),
    );
    return controls
      .filter((element) => element.offsetParent !== null)
      .map((element) => {
        const box = element.getBoundingClientRect();
        return { label: element.getAttribute("aria-label") ?? element.textContent?.trim() ?? element.tagName, width: box.width, height: box.height };
      })
      .filter((box) => box.height < 44 || box.width < 44);
  });
  expect(undersized).toEqual([]);
});

test("400% zoom (320 CSS px) reflows without horizontal scrolling", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 640 });
  await startReviewedSession(page);
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
});

test("keyboard focus is visible on sterile-mode controls", async ({ page }) => {
  await startReviewedSession(page);
  await page.keyboard.press("Tab");
  const outline = await page.evaluate(() => {
    const element = document.activeElement as HTMLElement | null;
    if (element === null || element === document.body) return "none";
    const style = getComputedStyle(element);
    return `${style.outlineStyle} ${style.outlineWidth} ${style.boxShadow}`;
  });
  expect(outline).not.toMatch(/^none 0px none$/);
});

test("reduced-motion preference disables animation", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await startReviewedSession(page);
  const animated = await page.evaluate(() =>
    Array.from(document.querySelectorAll<HTMLElement>("*"))
      .map((element) => getComputedStyle(element))
      .filter((style) => style.animationName !== "none" && parseFloat(style.animationDuration) > 0.01)
      .length,
  );
  expect(animated).toBe(0);
});
