import { beginLocalAssessmentWrite, finishLocalAssessmentWrite } from "./local-write-state";

const DATABASE_NAME = "clin-compass-assessments";
const DATABASE_VERSION = 2;

let databasePromise: Promise<IDBDatabase> | undefined;

/** Open the feature-owned IndexedDB database and create its stores on first use. */
export function openAssessmentDatabase(): Promise<IDBDatabase> {
  if (databasePromise) return databasePromise;

  databasePromise = new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains("definitions")) {
        database.createObjectStore("definitions", { keyPath: ["scale_id", "version"] });
      }
      if (!database.objectStoreNames.contains("drafts")) {
        const drafts = database.createObjectStore("drafts", { keyPath: ["owner_id", "scale_id"] });
        drafts.createIndex("owner_id", "owner_id");
      }
      if (!database.objectStoreNames.contains("outbox")) {
        const outbox = database.createObjectStore("outbox", {
          keyPath: ["owner_id", "submission_id"],
        });
        outbox.createIndex("owner_id", "owner_id");
        outbox.createIndex("owner_created", ["owner_id", "created_at"]);
      }
      if (!database.objectStoreNames.contains("meta")) {
        database.createObjectStore("meta", { keyPath: "key" });
      }
      createDiaryStores(database);
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
      reject(request.error ?? new Error("Could not open assessment storage"));
    };
    request.onblocked = () =>
      reject(new Error("Assessment storage upgrade is blocked by another tab"));
  });

  return databasePromise;
}

/** Add diary stores to the shared patient database without changing assessment stores. */
function createDiaryStores(database: IDBDatabase): void {
  if (!database.objectStoreNames.contains("diaryDrafts")) {
    const drafts = database.createObjectStore("diaryDrafts", {
      keyPath: ["owner_id", "draft_id"],
    });
    drafts.createIndex("owner_id", "owner_id");
    drafts.createIndex("owner_updated", ["owner_id", "updated_at"]);
    drafts.createIndex("owner_daily", ["owner_id", "daily_on"], { unique: true });
  }
  if (!database.objectStoreNames.contains("diaryEntries")) {
    const entries = database.createObjectStore("diaryEntries", {
      keyPath: ["owner_id", "entry_id"],
    });
    entries.createIndex("owner_id", "owner_id");
    entries.createIndex("owner_daily", ["owner_id", "daily_on"]);
    entries.createIndex("owner_occurred", ["owner_id", "occurred_at"]);
  }
  if (!database.objectStoreNames.contains("diaryOutbox")) {
    const outbox = database.createObjectStore("diaryOutbox", {
      keyPath: ["owner_id", "operation_id"],
    });
    outbox.createIndex("owner_id", "owner_id");
    outbox.createIndex("owner_created", ["owner_id", "created_at"]);
    outbox.createIndex("owner_entry_created", ["owner_id", "entry_id", "created_at"]);
  }
  if (!database.objectStoreNames.contains("diaryAudio")) {
    const audio = database.createObjectStore("diaryAudio", {
      keyPath: ["owner_id", "entry_id"],
    });
    audio.createIndex("owner_id", "owner_id");
  }
  if (!database.objectStoreNames.contains("intakeCache")) {
    const intake = database.createObjectStore("intakeCache", {
      keyPath: ["owner_id", "prescription_id", "scheduled_on", "dose_ordinal"],
    });
    intake.createIndex("owner_id", "owner_id");
    intake.createIndex("owner_date", ["owner_id", "scheduled_on"]);
  }
  if (!database.objectStoreNames.contains("intakeOutbox")) {
    const intakeOutbox = database.createObjectStore("intakeOutbox", {
      keyPath: ["owner_id", "operation_id"],
    });
    intakeOutbox.createIndex("owner_id", "owner_id");
    intakeOutbox.createIndex("owner_created", ["owner_id", "created_at"]);
  }
  if (!database.objectStoreNames.contains("diaryContext")) {
    database.createObjectStore("diaryContext", { keyPath: "owner_id" });
  }
}

/** Resolve only after the transaction commits; an abort rejects with its browser error. */
export function transactionComplete(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.addEventListener("complete", () => resolve(), { once: true });
    transaction.addEventListener(
      "abort",
      () => {
        reject(transaction.error ?? new Error("IndexedDB transaction aborted"));
      },
      { once: true },
    );
  });
}

/** Convert a native IndexedDB request into a promise without changing transaction timing. */
export function requestResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.addEventListener("success", () => resolve(request.result), { once: true });
    request.addEventListener(
      "error",
      () => {
        reject(request.error ?? new Error("IndexedDB request failed"));
      },
      { once: true },
    );
  });
}

/** Run a request sequence and preserve the original failure if the transaction aborts. */
export async function runTransaction<T>(
  database: IDBDatabase,
  stores: string[],
  mode: IDBTransactionMode,
  action: (transaction: IDBTransaction) => Promise<T>,
): Promise<T> {
  const transaction = database.transaction(stores, mode);
  const completed = transactionComplete(transaction);
  const tracksWrite = mode === "readwrite";
  if (tracksWrite) beginLocalAssessmentWrite();
  try {
    const result = await action(transaction);
    await completed;
    return result;
  } catch (error) {
    try {
      transaction.abort();
    } catch (abortError) {
      if (!(abortError instanceof DOMException && abortError.name === "InvalidStateError")) {
        throw abortError;
      }
    }
    await completed.catch(() => undefined);
    throw error;
  } finally {
    if (tracksWrite) finishLocalAssessmentWrite();
  }
}
