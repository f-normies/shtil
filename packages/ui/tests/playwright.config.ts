import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "@playwright/test";

const WORKTREE_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");

export default defineConfig({
  testDir: ".",
  testMatch: [
    "components.spec.ts",
    "chat.spec.ts",
    "visual-regressions.spec.ts",
    "popover-placement.spec.ts",
    "visual-review-icons.spec.ts",
    "visual-review-components.spec.ts",
  ],
  fullyParallel: false,
  workers: 1,
  outputDir: "/private/tmp/cc33-ui-playwright",
  use: {
    baseURL: "http://127.0.0.1:4179",
    browserName: "chromium",
    headless: true,
    serviceWorkers: "block",
  },
  webServer: {
    command:
      "pnpm --dir apps/patient exec vite ../../packages/ui/tests/fixture --config ../../packages/ui/tests/fixture/vite.config.ts --host 127.0.0.1 --port 4179 --strictPort",
    cwd: WORKTREE_ROOT,
    url: "http://127.0.0.1:4179/",
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
