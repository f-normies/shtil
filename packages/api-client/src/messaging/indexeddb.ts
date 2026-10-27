import type { MessagingDraft, MessagingOutboxItem, MessagingPersistence } from "./local";

const DATABASE_NAME = "clin-compass-messaging-local";
const DATABASE_VERSION = 1;
const DRAFTS = "drafts";
const OUTBOX = "outbox";
const OWNER_INDEX = "ownerId";

export function createIndexedDbMessagingPersistence(): MessagingPersistence {
  let database: Promise<IDBDatabase> | undefined;
  const open = (): Promise<IDBDatabase> => {
    if (typeof indexedDB === "undefined") {
      return Promise.reject(new Error("IndexedDB is unavailable; the message was not saved."));
    }
    database ??= new Promise((resolve, reject) => {
      const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(DRAFTS)) {
          const store = db.createObjectStore(DRAFTS, { keyPath: "key" });
          store.createIndex(OWNER_INDEX, OWNER_INDEX, { unique: false });
        }
        if (!db.objectStoreNames.contains(OUTBOX)) {
          const store = db.createObjectStore(OUTBOX, { keyPath: "key" });
          store.createIndex(OWNER_INDEX, OWNER_INDEX, { unique: false });
        }
      };
      request.onsuccess = () => {
        const db = request.result;
        db.onversionchange = () => db.close();
        resolve(db);
      };
      request.onerror = () =>
        reject(request.error ?? new Error("Could not open messaging storage."));
      request.onblocked = () => reject(new Error("Messaging storage upgrade is blocked."));
    });
    return database;
  };

  return {
    async putDraft(record) {
      await write(DRAFTS, (store) =>
        store.put({
          ...record,
          key: draftKey(record.ownerId, record.patientId, record.expectedDoctorId),
        }),
      );
    },
    async getDraft(ownerId, patientId, expectedDoctorId) {
      const record = await requestValue<StoredDraft | undefined>(DRAFTS, (store) =>
        store.get(draftKey(ownerId, patientId, expectedDoctorId)),
      );
      return record ? withoutKey(record) : null;
    },
    async listDrafts(ownerId) {
      const records = await requestValue<StoredDraft[]>(DRAFTS, (store) =>
        store.index(OWNER_INDEX).getAll(ownerId),
      );
      return records.map(withoutKey);
    },
    async deleteDraft(ownerId, patientId, expectedDoctorId) {
      await write(DRAFTS, (store) => store.delete(draftKey(ownerId, patientId, expectedDoctorId)));
    },
    async putOutbox(record) {
      await write(OUTBOX, (store) =>
        store.put({ ...record, key: outboxKey(record.ownerId, record.clientMessageId) }),
      );
    },
    async getOutbox(ownerId, clientMessageId) {
      const record = await requestValue<StoredOutbox | undefined>(OUTBOX, (store) =>
        store.get(outboxKey(ownerId, clientMessageId)),
      );
      return record ? withoutKey(record) : null;
    },
    async listOutbox(ownerId) {
      const records = await requestValue<StoredOutbox[]>(OUTBOX, (store) =>
        store.index(OWNER_INDEX).getAll(ownerId),
      );
      return records.map(withoutKey);
    },
    async deleteOutbox(ownerId, clientMessageId) {
      await write(OUTBOX, (store) => store.delete(outboxKey(ownerId, clientMessageId)));
    },
    async deleteOwner(ownerId) {
      const db = await open();
      await new Promise<void>((resolve, reject) => {
        const transaction = db.transaction([DRAFTS, OUTBOX], "readwrite");
        const drafts = transaction.objectStore(DRAFTS).index(OWNER_INDEX).getAllKeys(ownerId);
        const outbox = transaction.objectStore(OUTBOX).index(OWNER_INDEX).getAllKeys(ownerId);
        drafts.onsuccess = () => {
          for (const key of drafts.result) transaction.objectStore(DRAFTS).delete(key);
        };
        outbox.onsuccess = () => {
          for (const key of outbox.result) transaction.objectStore(OUTBOX).delete(key);
        };
        transaction.oncomplete = () => resolve();
        transaction.onerror = () =>
          reject(transaction.error ?? new Error("Could not clear messaging storage."));
        transaction.onabort = () =>
          reject(transaction.error ?? new Error("Could not clear messaging storage."));
      });
    },
  };

  async function requestValue<T>(
    storeName: string,
    createRequest: (store: IDBObjectStore) => IDBRequest,
  ): Promise<T> {
    const db = await open();
    return new Promise<T>((resolve, reject) => {
      const transaction = db.transaction(storeName, "readonly");
      const request = createRequest(transaction.objectStore(storeName));
      let result: T;
      request.onsuccess = () => {
        result = request.result as T;
      };
      request.onerror = () =>
        reject(request.error ?? new Error("Could not read messaging storage."));
      transaction.oncomplete = () => resolve(result);
      transaction.onerror = () =>
        reject(transaction.error ?? new Error("Could not read messaging storage."));
      transaction.onabort = () =>
        reject(transaction.error ?? new Error("Could not read messaging storage."));
    });
  }

  async function write(
    storeName: string,
    createRequest: (store: IDBObjectStore) => IDBRequest,
  ): Promise<void> {
    const db = await open();
    await new Promise<void>((resolve, reject) => {
      const transaction = db.transaction(storeName, "readwrite");
      createRequest(transaction.objectStore(storeName));
      transaction.oncomplete = () => resolve();
      transaction.onerror = () =>
        reject(transaction.error ?? new Error("Could not write messaging storage."));
      transaction.onabort = () =>
        reject(transaction.error ?? new Error("Could not write messaging storage."));
    });
  }
}

type StoredDraft = MessagingDraft & { key: string };
type StoredOutbox = MessagingOutboxItem & { key: string };

function draftKey(ownerId: string, patientId: string, doctorId: string): string {
  return `${ownerId}/${patientId}/${doctorId}`;
}

function outboxKey(ownerId: string, clientMessageId: string): string {
  return `${ownerId}/${clientMessageId}`;
}

function withoutKey<T extends { key: string }>(record: T): Omit<T, "key"> {
  const { key: _key, ...value } = record;
  return value;
}
