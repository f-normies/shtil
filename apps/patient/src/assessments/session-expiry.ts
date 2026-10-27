import { denyOfflineAssessmentAccess } from "./offline-access";
import type { AssessmentStore } from "./store";

/**
 * Lock the local answers of the patient whose session has just ended.
 * The id comes from the expired session: a newer owner saved by another tab stays unlocked.
 * If the lock cannot be written, offline access is denied until the next online login.
 * Drafts and the send queue stay on the device.
 */
export async function lockLocalAccessOnExpiry(
  openStore: () => Promise<Pick<AssessmentStore, "lockOwnerPointer">>,
  expiredOwnerId: string | null,
): Promise<void> {
  if (!expiredOwnerId) return;
  try {
    const store = await openStore();
    await store.lockOwnerPointer(expiredOwnerId);
  } catch {
    denyOfflineAssessmentAccess();
  }
}
