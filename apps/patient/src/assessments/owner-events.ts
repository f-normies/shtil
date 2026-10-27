const CHANNEL_NAME = "clin-compass-assessment-owner";
let channel: BroadcastChannel | undefined;

function getChannel(): BroadcastChannel | null {
  if (typeof window === "undefined" || typeof BroadcastChannel === "undefined") return null;
  channel ??= new BroadcastChannel(CHANNEL_NAME);
  return channel;
}

/** Notify other patient tabs after a committed owner-pointer change. */
export function publishOwnerChange(): void {
  getChannel()?.postMessage({ type: "owner-changed" });
}

/** Notify other patient tabs when local access must be denied after a failed lock write. */
export function publishOfflineAccessDenial(): void {
  getChannel()?.postMessage({ type: "offline-access-denied" });
}

/** Subscribe to owner changes committed by another patient tab. */
export function subscribeToOwnerChanges(onChange: () => void): () => void {
  return subscribeToMessage("owner-changed", onChange);
}

/** Subscribe to a failed lock write that requires other patient tabs to revalidate. */
export function subscribeToOfflineAccessDenials(onDenial: () => void): () => void {
  return subscribeToMessage("offline-access-denied", onDenial);
}

function subscribeToMessage(type: string, onMessage: () => void): () => void {
  const ownerChannel = getChannel();
  if (!ownerChannel) return () => {};
  const handleMessage = (event: MessageEvent<unknown>) => {
    if (
      event.data &&
      typeof event.data === "object" &&
      "type" in event.data &&
      event.data.type === type
    ) {
      onMessage();
    }
  };
  ownerChannel.addEventListener("message", handleMessage);
  return () => ownerChannel.removeEventListener("message", handleMessage);
}
