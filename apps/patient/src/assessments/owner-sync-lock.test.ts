import { expect, it } from "vitest";
import { withOwnerSyncLock } from "./owner-sync-lock";

function deferred(): { promise: Promise<void>; resolve: () => void } {
  let resolve = () => {};
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

it("serializes sync for one owner while allowing another owner to proceed", async () => {
  const holdOwnerA = deferred();
  const ownerAStarted = deferred();
  const ownerBStarted = deferred();
  let secondOwnerAStarted = false;

  const firstA = withOwnerSyncLock("owner-a", async () => {
    ownerAStarted.resolve();
    await holdOwnerA.promise;
  });
  await ownerAStarted.promise;
  const secondA = withOwnerSyncLock("owner-a", async () => {
    secondOwnerAStarted = true;
  });
  const firstB = withOwnerSyncLock("owner-b", async () => ownerBStarted.resolve());

  await ownerBStarted.promise;
  expect(secondOwnerAStarted).toBe(false);
  holdOwnerA.resolve();
  await Promise.all([firstA, secondA, firstB]);
  expect(secondOwnerAStarted).toBe(true);
});
