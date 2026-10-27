import { expect, it, vi } from "vitest";
import type { DiarySyncOutcome } from "./sync";
import { syncAfterLocalWrite } from "./sync-after-write";

function outcome(confirmed: string[]): DiarySyncOutcome {
  return {
    status: "complete",
    sessionGeneration: 1,
    submitted: confirmed.length,
    blocked: 0,
    confirmedOperationIds: confirmed,
  };
}

it("starts the outbox sync right after a local write and refreshes local records once something was accepted", async () => {
  const syncNow = vi.fn(async () => outcome(["op-1"]));
  const refreshLocal = vi.fn(async () => undefined);
  await syncAfterLocalWrite({ syncNow, refreshLocal });
  expect(syncNow).toHaveBeenCalledOnce();
  expect(refreshLocal).toHaveBeenCalledOnce();
});

it("leaves local records alone when nothing reached the server", async () => {
  const refreshLocal = vi.fn(async () => undefined);
  await syncAfterLocalWrite({ syncNow: async () => outcome([]), refreshLocal });
  await syncAfterLocalWrite({ syncNow: async () => null, refreshLocal });
  expect(refreshLocal).not.toHaveBeenCalled();
});

it("swallows sync failures so the saved screen still opens", async () => {
  await expect(
    syncAfterLocalWrite({
      syncNow: async () => {
        throw new Error("network");
      },
      refreshLocal: async () => undefined,
    }),
  ).resolves.toBeUndefined();
});
