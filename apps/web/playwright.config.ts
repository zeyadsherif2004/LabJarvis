import { defineConfig, devices } from "@playwright/test";


// The zero-touch journey needs an API started with the hands-free profile, so
// scripts/run_e2e.py runs it as a second pass with LABJARVIS_E2E_PROFILE set.
const handsFreeProfile = process.env.LABJARVIS_E2E_PROFILE === "hands-free";

export default defineConfig({
  testDir: "./e2e",
  ...(handsFreeProfile
    ? { testMatch: "**/zero-touch.spec.ts" }
    : { testIgnore: ["**/screenshots.spec.ts", "**/zero-touch.spec.ts"] }),
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "line",
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  outputDir: "../../output/playwright",
});
