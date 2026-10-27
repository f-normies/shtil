import { publishOfflineAccessDenial } from "./owner-events";

const DENIED_KEY = "assessment_offline_access_denied";
const DENIAL_MESSAGE =
  "Не удалось сохранить блокировку доступа. Подключитесь к интернету и войдите снова.";
let deniedInMemory = false;

/** Deny cached patient access until a new online session is confirmed. */
export function denyOfflineAssessmentAccess(): void {
  const alreadyDenied = isOfflineAssessmentAccessDenied();
  deniedInMemory = true;
  for (const storage of browserStorages()) writeMarker(storage);
  if (!alreadyDenied) publishOfflineAccessDenial();
}

/** Remember a denial received from another tab without broadcasting it again. */
export function observeOfflineAssessmentAccessDenial(): void {
  deniedInMemory = true;
  for (const storage of browserStorages()) writeMarker(storage);
}

/** Whether a prior failed 401 lock write requires online verification. */
export function isOfflineAssessmentAccessDenied(): boolean {
  return deniedInMemory || browserStorages().some(readMarker);
}

/** Clear the denial only after the current online owner was committed successfully. */
export function clearOfflineAssessmentAccessDenial(): void {
  deniedInMemory = false;
  for (const storage of browserStorages()) removeMarker(storage);
}

/** Message displayed while the patient is asked to re-establish an online session. */
export function getOfflineAssessmentDenialMessage(): string | null {
  return isOfflineAssessmentAccessDenied() ? DENIAL_MESSAGE : null;
}

function writeMarker(storage: Storage): void {
  try {
    storage.setItem(DENIED_KEY, "true");
  } catch {
    // The in-memory denial still closes this page when browser storage is unavailable.
  }
}

function browserStorages(): Storage[] {
  if (typeof window === "undefined") return [];
  const storages: Storage[] = [];
  for (const key of ["localStorage", "sessionStorage"] as const) {
    try {
      storages.push(window[key]);
    } catch {
      // Try the other browser storage backend.
    }
  }
  return storages;
}

function readMarker(storage: Storage): boolean {
  try {
    return storage.getItem(DENIED_KEY) === "true";
  } catch {
    return false;
  }
}

function removeMarker(storage: Storage): void {
  try {
    storage.removeItem(DENIED_KEY);
  } catch {
    // A stale marker only denies offline access; a successful online check is authoritative.
  }
}
