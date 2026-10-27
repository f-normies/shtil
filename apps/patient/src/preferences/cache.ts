import type { Schemas } from "@compass/api-client";
import { isOfflineAssessmentAccessDenied } from "../assessments/offline-access";
import { AssessmentStore } from "../assessments/store";
import type { OwnerPointer } from "../assessments/types";
import { canReadCachedPreferences } from "./preferences-access";

const DATABASE_NAME = "clin-compass-preferences";
const DATABASE_VERSION = 1;

export type PreferencesCacheRecord = {
  owner_id: string;
  preferences: Schemas["PatientPreferencesOut"];
  prescriptions?: Schemas["ScheduleItem"][];
  consent?: Schemas["AcceptedConsentOut"];
};

let databasePromise: Promise<IDBDatabase> | undefined;

function openPreferencesDatabase(): Promise<IDBDatabase> {
  if (databasePromise) return databasePromise;
  if (typeof indexedDB === "undefined") return Promise.reject(new Error("IndexedDB unavailable"));

  databasePromise = new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains("profiles")) {
        database.createObjectStore("profiles", { keyPath: "owner_id" });
      }
    };
    request.onsuccess = () => {
      const database = request.result;
      database.onversionchange = () => {
        database.close();
        databasePromise = undefined;
      };
      resolve(database);
    };
    request.onerror = () => {
      databasePromise = undefined;
      reject(request.error ?? new Error("Could not open patient preferences storage"));
    };
    request.onblocked = () => reject(new Error("Patient preferences storage upgrade is blocked"));
  });
  return databasePromise;
}

function readRecord(
  database: IDBDatabase,
  ownerId: string,
): Promise<PreferencesCacheRecord | null> {
  return new Promise((resolve, reject) => {
    const transaction = database.transaction("profiles", "readonly");
    const request = transaction.objectStore("profiles").get(ownerId) as IDBRequest<
      PreferencesCacheRecord | undefined
    >;
    request.onsuccess = () => resolve(request.result ?? null);
    request.onerror = () =>
      reject(request.error ?? new Error("Could not read patient preferences"));
    transaction.onabort = () => reject(transaction.error ?? new Error("Preference read aborted"));
  });
}

function writeRecord(database: IDBDatabase, record: PreferencesCacheRecord): Promise<void> {
  return new Promise((resolve, reject) => {
    const transaction = database.transaction("profiles", "readwrite");
    transaction.objectStore("profiles").put(record);
    transaction.oncomplete = () => resolve();
    transaction.onerror = () =>
      reject(transaction.error ?? new Error("Could not cache preferences"));
    transaction.onabort = () => reject(transaction.error ?? new Error("Preference write aborted"));
  });
}

async function currentOwnerPointer(): Promise<OwnerPointer | null> {
  return (await AssessmentStore.open()).getOwnerPointer();
}

/** Read cached settings only for the current, confirmed, unlocked patient. */
export async function readCachedPatientPreferences(
  ownerId: string,
): Promise<PreferencesCacheRecord | null> {
  const pointer = await currentOwnerPointer();
  if (!canReadCachedPreferences(pointer, ownerId, isOfflineAssessmentAccessDenied())) return null;
  const record = await readRecord(await openPreferencesDatabase(), ownerId);
  return record?.owner_id === ownerId ? record : null;
}

/** Save a successful server response under the owner already confirmed by the session guard. */
export async function cachePatientPreferences(
  ownerId: string,
  update: Partial<Omit<PreferencesCacheRecord, "owner_id">>,
): Promise<void> {
  const pointer = await currentOwnerPointer();
  if (!canReadCachedPreferences(pointer, ownerId, isOfflineAssessmentAccessDenied())) {
    throw new Error("Patient preferences are not available for this session");
  }
  const database = await openPreferencesDatabase();
  const existing = await readRecord(database, ownerId);
  const next: PreferencesCacheRecord = {
    ...existing,
    ...update,
    owner_id: ownerId,
    preferences: update.preferences ?? (existing?.preferences as Schemas["PatientPreferencesOut"]),
  };
  if (!next.preferences)
    throw new Error("Patient preferences are required before caching related data");
  await writeRecord(database, next);
}

/** Remove this owner's cached preferences after they confirm local-data deletion. */
export async function clearCachedPatientPreferences(ownerId: string): Promise<void> {
  const pointer = await currentOwnerPointer();
  if (pointer?.role !== "patient" || pointer.id !== ownerId) return;
  const database = await openPreferencesDatabase();
  await new Promise<void>((resolve, reject) => {
    const transaction = database.transaction("profiles", "readwrite");
    transaction.objectStore("profiles").delete(ownerId);
    transaction.oncomplete = () => resolve();
    transaction.onerror = () =>
      reject(transaction.error ?? new Error("Could not clear preferences"));
    transaction.onabort = () =>
      reject(transaction.error ?? new Error("Preference deletion aborted"));
  });
}

/** Whether this confirmed patient has any profile cache, even without cached scales. */
export async function hasCachedPatientPreferences(ownerId: string): Promise<boolean> {
  try {
    return (await readCachedPatientPreferences(ownerId)) !== null;
  } catch {
    return false;
  }
}
