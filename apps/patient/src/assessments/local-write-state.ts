let activeWrites = 0;
const listeners = new Set<() => void>();

export function subscribeToLocalAssessmentWrites(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Whether one or more IndexedDB writes are still running. */
export function localAssessmentWritePending(): boolean {
  return activeWrites > 0;
}

function notify(): void {
  for (const listener of listeners) listener();
}

/** Mark an IndexedDB write that must finish before the PWA can be updated. */
export function beginLocalAssessmentWrite(): void {
  activeWrites += 1;
  notify();
}

/** Release one finished IndexedDB write. */
export function finishLocalAssessmentWrite(): void {
  activeWrites = Math.max(0, activeWrites - 1);
  notify();
}
