import { defineConfig } from "@playwright/test";

const resultsDirectory = process.env.E2E_RESULTS_DIR;
const htmlReportDirectory = process.env.E2E_HTML_REPORT_DIR;

if (!resultsDirectory || !htmlReportDirectory || !process.env.E2E_REPORT_PATH) {
  throw new Error("Run E2E through `pnpm e2e` so setup and reporting are applied");
}

export default defineConfig({
  testDir: ".",
  testMatch: "m1-assessment-realtime.spec.ts",
  outputDir: resultsDirectory,
  timeout: 120_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ["list"],
    ["html", { outputFolder: htmlReportDirectory, open: "never" }],
    ["./reporter.ts"],
  ],
  use: {
    baseURL: "http://127.0.0.1:5173/doctor/",
    browserName: "chromium",
    serviceWorkers: "block",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    actionTimeout: 15_000,
    navigationTimeout: 20_000,
  },
});
