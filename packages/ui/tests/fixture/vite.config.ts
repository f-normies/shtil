import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const fixtureDirectory = dirname(fileURLToPath(import.meta.url));
const worktreeRoot = resolve(fixtureDirectory, "../../../..");
const requireFromPatient = createRequire(resolve(worktreeRoot, "apps/patient/package.json"));
const react = requireFromPatient("@vitejs/plugin-react").default;
const tailwindcss = requireFromPatient("@tailwindcss/vite").default;
const reactQueryEntry = resolve(
  worktreeRoot,
  "apps/patient/node_modules/@tanstack/react-query/build/modern/index.js",
);

export default {
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@tanstack/react-query": reactQueryEntry },
  },
};
