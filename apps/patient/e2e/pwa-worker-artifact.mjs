import { readFile, writeFile } from "node:fs/promises";

const serviceWorkerArtifact = new URL("../dist/sw.js", import.meta.url);

/** Change only the ignored build output so a real browser update check installs a new worker. */
export async function appendSyntheticWorkerRevision() {
  const original = await readFile(serviceWorkerArtifact, "utf8");
  await writeFile(serviceWorkerArtifact, `${original}\n/* synthetic update ${Date.now()} */\n`);
  return original;
}

/** Restore the build output after the isolated update test. */
export function restoreWorkerArtifact(original) {
  return writeFile(serviceWorkerArtifact, original);
}
