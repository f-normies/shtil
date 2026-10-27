type BrowserLockManager = {
  request<T>(name: string, callback: () => Promise<T>): Promise<T>;
};

const ownerQueues = new Map<string, Promise<void>>();

/** Serialize every patient outbox runner in this tab and coordinate tabs with Web Locks. */
export async function withOwnerSyncLock<T>(
  ownerId: string,
  operation: () => Promise<T>,
): Promise<T> {
  if (!ownerId) throw new Error("A patient owner is required for synchronization");
  const previous = ownerQueues.get(ownerId);
  let release = () => {};
  const current = new Promise<void>((resolve) => {
    release = resolve;
  });
  ownerQueues.set(ownerId, current);
  if (previous) await previous;

  try {
    const lockManager =
      typeof navigator === "undefined"
        ? undefined
        : (navigator as Navigator & { locks?: BrowserLockManager }).locks;
    if (!lockManager) return await operation();
    return await lockManager.request(`clin-compass-owner-sync:${ownerId}`, operation);
  } finally {
    release();
    if (ownerQueues.get(ownerId) === current) ownerQueues.delete(ownerId);
  }
}
