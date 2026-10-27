import type { Schemas } from "@compass/api-client";
import { requestResult, runTransaction } from "../assessments/database";
import { ownerFrom, requireOwner } from "../assessments/store-owner";
import type { DiaryContextCacheRecord, DiaryScheduleSnapshot } from "./types";

/** Cache a successfully loaded context while retaining the last successful schedule. */
export function cacheDiaryContext(
  database: IDBDatabase,
  ownerId: string,
  context: Schemas["DiaryContextOut"],
): Promise<DiaryContextCacheRecord> {
  return runTransaction(database, ["meta", "diaryContext"], "readwrite", async (transaction) => {
    requireOwner(await ownerFrom(transaction), ownerId);
    const contexts = transaction.objectStore("diaryContext");
    const previous = await requestResult(
      contexts.get(ownerId) as IDBRequest<DiaryContextCacheRecord | undefined>,
    );
    const record: DiaryContextCacheRecord = {
      owner_id: ownerId,
      context: structuredClone(context),
      context_fetched_at: new Date().toISOString(),
      schedule_snapshot: previous?.schedule_snapshot ?? null,
      prescriptions_snapshot: previous?.prescriptions_snapshot ?? null,
      prescriptions_fetched_at: previous?.prescriptions_fetched_at ?? null,
      latest_message: previous?.latest_message ?? null,
      latest_message_fetched_at: previous?.latest_message_fetched_at ?? null,
    };
    await requestResult(contexts.put(record));
    return record;
  });
}

/** Cache only a successful server schedule with its fetch time for stale detection. */
export function cacheDiarySchedule(
  database: IDBDatabase,
  ownerId: string,
  schedule: Schemas["ScheduleOut"],
): Promise<DiaryContextCacheRecord> {
  return runTransaction(database, ["meta", "diaryContext"], "readwrite", async (transaction) => {
    requireOwner(await ownerFrom(transaction), ownerId);
    const contexts = transaction.objectStore("diaryContext");
    const previous = await requestResult(
      contexts.get(ownerId) as IDBRequest<DiaryContextCacheRecord | undefined>,
    );
    const scheduleSnapshot: DiaryScheduleSnapshot = {
      ...structuredClone(schedule),
      fetched_at: new Date().toISOString(),
    };
    const record: DiaryContextCacheRecord = {
      owner_id: ownerId,
      context: previous?.context ?? null,
      context_fetched_at: previous?.context_fetched_at ?? null,
      schedule_snapshot: scheduleSnapshot,
      prescriptions_snapshot: previous?.prescriptions_snapshot ?? null,
      prescriptions_fetched_at: previous?.prescriptions_fetched_at ?? null,
      latest_message: previous?.latest_message ?? null,
      latest_message_fetched_at: previous?.latest_message_fetched_at ?? null,
    };
    await requestResult(contexts.put(record));
    return record;
  });
}

/** Cache actual current prescriptions only after a successful authenticated response. */
export function cacheDiaryPrescriptions(
  database: IDBDatabase,
  ownerId: string,
  prescriptions: readonly Schemas["ScheduleItem"][],
): Promise<DiaryContextCacheRecord> {
  return runTransaction(database, ["meta", "diaryContext"], "readwrite", async (transaction) => {
    requireOwner(await ownerFrom(transaction), ownerId);
    const contexts = transaction.objectStore("diaryContext");
    const previous = await requestResult(
      contexts.get(ownerId) as IDBRequest<DiaryContextCacheRecord | undefined>,
    );
    const record: DiaryContextCacheRecord = {
      owner_id: ownerId,
      context: previous?.context ?? null,
      context_fetched_at: previous?.context_fetched_at ?? null,
      schedule_snapshot: previous?.schedule_snapshot ?? null,
      prescriptions_snapshot: structuredClone([...prescriptions]),
      prescriptions_fetched_at: new Date().toISOString(),
      latest_message: previous?.latest_message ?? null,
      latest_message_fetched_at: previous?.latest_message_fetched_at ?? null,
    };
    await requestResult(contexts.put(record));
    return record;
  });
}

/** Cache the latest real patient-visible message; null records a successful empty history. */
export function cacheDiaryLatestMessage(
  database: IDBDatabase,
  ownerId: string,
  message: Schemas["MessageOut"] | null,
): Promise<DiaryContextCacheRecord> {
  return runTransaction(database, ["meta", "diaryContext"], "readwrite", async (transaction) => {
    requireOwner(await ownerFrom(transaction), ownerId);
    const contexts = transaction.objectStore("diaryContext");
    const previous = await requestResult(
      contexts.get(ownerId) as IDBRequest<DiaryContextCacheRecord | undefined>,
    );
    const record: DiaryContextCacheRecord = {
      owner_id: ownerId,
      context: previous?.context ?? null,
      context_fetched_at: previous?.context_fetched_at ?? null,
      schedule_snapshot: previous?.schedule_snapshot ?? null,
      prescriptions_snapshot: previous?.prescriptions_snapshot ?? null,
      prescriptions_fetched_at: previous?.prescriptions_fetched_at ?? null,
      latest_message: message ? structuredClone(message) : null,
      latest_message_fetched_at: new Date().toISOString(),
    };
    await requestResult(contexts.put(record));
    return record;
  });
}

/** Restore the context and last successful schedule for the unlocked owner. */
export function getDiaryContextCache(
  database: IDBDatabase,
  ownerId: string,
): Promise<DiaryContextCacheRecord | null> {
  return runTransaction(database, ["meta", "diaryContext"], "readonly", async (transaction) => {
    requireOwner(await ownerFrom(transaction), ownerId);
    const record = await requestResult(
      transaction.objectStore("diaryContext").get(ownerId) as IDBRequest<
        DiaryContextCacheRecord | undefined
      >,
    );
    return record ?? null;
  });
}
