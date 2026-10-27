import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: ["assessment-pwa.spec.ts", "brand-pwa.spec.ts"],
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:5175",
    browserName: "chromium",
    headless: true,
    serviceWorkers: "allow",
  },
  webServer: [
    {
      command: "pnpm build && pnpm preview --host 127.0.0.1 --port 5175 --strictPort",
      url: "http://127.0.0.1:5175/patient/",
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command:
        "pnpm --dir ../doctor build && pnpm --dir ../doctor preview --host 127.0.0.1 --port 5176 --strictPort",
      url: "http://127.0.0.1:5176/doctor/",
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
