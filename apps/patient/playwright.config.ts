import { defineConfig } from "@playwright/test";

const browserEnvironment = (
  globalThis as typeof globalThis & {
    process?: { env?: Record<string, string | undefined> };
  }
).process?.env;

function browserPort(environmentName: string, fallback: number): number {
  const value = browserEnvironment?.[environmentName];
  const parsed = value ? Number(value) : fallback;
  return Number.isInteger(parsed) && parsed >= 1024 && parsed <= 65535 ? parsed : fallback;
}

const doctorPort = browserPort("COMPASS_DOCTOR_BROWSER_PORT", 5173);
const patientPort = browserPort("COMPASS_PATIENT_BROWSER_PORT", 5174);

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.spec.ts",
  testIgnore: ["**/assessment-pwa.spec.ts", "**/brand-pwa.spec.ts"],
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: `http://127.0.0.1:${patientPort}`,
    browserName: "chromium",
    headless: true,
    serviceWorkers: "block",
  },
  webServer: [
    {
      command: `pnpm --dir ../doctor exec vite --host 127.0.0.1 --port ${doctorPort} --strictPort`,
      url: `http://127.0.0.1:${doctorPort}/doctor/login`,
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: `pnpm exec vite --host 127.0.0.1 --port ${patientPort} --strictPort`,
      url: `http://127.0.0.1:${patientPort}/patient/`,
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
});
