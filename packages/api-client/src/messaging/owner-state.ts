import type { MessagingOwnerState, MessagingSessionMarker } from "./local";

const OWNER_STATE_KEY = "clin-compass:messaging-session:v1";
let defaultOwnerState: MessagingOwnerState | undefined;

export function createMessagingId(): string {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function createBrowserMessagingOwnerState(): MessagingOwnerState {
  if (defaultOwnerState) return defaultOwnerState;
  const listeners = new Set<(marker: MessagingSessionMarker | null) => void>();
  const storage = typeof window === "undefined" ? undefined : window.localStorage;
  const read = (): MessagingSessionMarker | null => {
    if (!storage) return null;
    const raw = storage.getItem(OWNER_STATE_KEY);
    if (!raw) return null;
    try {
      const marker: unknown = JSON.parse(raw);
      return isSessionMarker(marker) ? marker : null;
    } catch {
      return null;
    }
  };
  const onStorage = (event: StorageEvent) => {
    if (event.key !== OWNER_STATE_KEY) return;
    const marker = read();
    for (const listener of listeners) listener(marker);
  };
  if (typeof window !== "undefined") window.addEventListener("storage", onStorage);
  defaultOwnerState = {
    read,
    write(marker) {
      if (!storage) throw new Error("Messaging session storage is unavailable.");
      storage.setItem(OWNER_STATE_KEY, JSON.stringify(marker));
      for (const listener of listeners) listener(marker);
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
  return defaultOwnerState;
}

function isSessionMarker(value: unknown): value is MessagingSessionMarker {
  if (
    !value ||
    typeof value !== "object" ||
    !("revision" in value) ||
    typeof value.revision !== "string" ||
    !("locked" in value)
  )
    return false;
  if (value.locked === true) return true;
  return (
    value.locked === false &&
    "ownerId" in value &&
    typeof value.ownerId === "string" &&
    "role" in value &&
    (value.role === "doctor" || value.role === "patient")
  );
}
