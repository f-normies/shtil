import type { DiarySyncOutcome } from "./sync";

/**
 * Send the outbox right after a local write instead of waiting for a route change.
 * Local records are refreshed only when the server accepted something, so the
 * "Отправляется…" line gives way to the delivered state without a reload.
 */
export async function syncAfterLocalWrite(input: {
  syncNow: () => Promise<DiarySyncOutcome | null>;
  refreshLocal: () => Promise<void>;
}): Promise<void> {
  let outcome: DiarySyncOutcome | null;
  try {
    outcome = await input.syncNow();
  } catch {
    return;
  }
  if (outcome && outcome.confirmedOperationIds.length > 0) {
    await input.refreshLocal().catch(() => undefined);
  }
}
