import { requestResult, runTransaction } from "../assessments/database";
import { ownerFrom, requireOwner } from "../assessments/store-owner";
import type { DiaryEntryRecord } from "./merge";
import {
  addDiaryEntryCommand,
  diaryCommandRecord,
  diaryEntryFromCommand,
  diaryReplayOrConflict,
  nextDiaryCommandTime,
} from "./outbox-command-helpers";
import type {
  DiaryCreateCommand,
  DiaryHideCommand,
  DiaryScheduleSnapshot,
  DiarySurveyStatus,
  DiaryUpdateCommand,
  EnqueueDiaryCommandResult,
  EnqueueDiaryEntryResult,
} from "./types";

function validateCreate(payload: DiaryCreateCommand): void {
  if (!payload.entry_id || !payload.operation_id) {
    throw new Error("Diary entry and operation IDs are required");
  }
  if (!Number.isFinite(Date.parse(payload.occurred_at))) {
    throw new Error("Diary event time must be valid");
  }
  if (payload.kind === "daily" && (!payload.daily_on || !payload.mood_level)) {
    throw new Error("A daily diary entry requires a date and mood level");
  }
  if (
    payload.mood_level != null &&
    (!Number.isInteger(payload.mood_level) || payload.mood_level < 1 || payload.mood_level > 5)
  ) {
    throw new Error("Mood level must be an integer from 1 to 5");
  }
}

type CreateMetadata = {
  surveyStatus?: DiarySurveyStatus;
  surveySubmissionIds?: string[];
  scheduleSnapshot?: DiaryScheduleSnapshot | null;
};

async function queueCreate(
  transaction: IDBTransaction,
  ownerId: string,
  payload: DiaryCreateCommand,
  metadata?: CreateMetadata,
): Promise<EnqueueDiaryEntryResult> {
  const replay = await diaryReplayOrConflict(transaction, {
    ownerId,
    operationId: payload.operation_id,
    entryId: payload.entry_id,
    action: "create",
    payload,
  });
  if (replay) return replay as EnqueueDiaryEntryResult;
  if (payload.kind === "daily" && payload.daily_on) {
    const daily = await requestResult(
      transaction
        .objectStore("diaryEntries")
        .index("owner_daily")
        .get([ownerId, payload.daily_on]) as IDBRequest<DiaryEntryRecord | undefined>,
    );
    if (daily) return { status: "daily-exists", currentEntry: daily };
  }
  return (await addDiaryEntryCommand(transaction, {
    ownerId,
    entry: diaryEntryFromCommand(ownerId, payload, metadata),
    action: "create",
    payload,
  })) as EnqueueDiaryEntryResult;
}

/** Queue a create command and its optimistic entry in one transaction. */
export function enqueueDiaryEntry(
  database: IDBDatabase,
  input: { ownerId: string; payload: DiaryCreateCommand; metadata?: CreateMetadata },
): Promise<EnqueueDiaryEntryResult> {
  validateCreate(input.payload);
  const payload = structuredClone(input.payload);
  return runTransaction(
    database,
    ["meta", "diaryEntries", "diaryOutbox"],
    "readwrite",
    async (transaction) => {
      requireOwner(await ownerFrom(transaction), input.ownerId);
      return queueCreate(transaction, input.ownerId, payload, input.metadata);
    },
  );
}

/** Queue a create command inside the transaction that commits a daily check-in. */
export async function enqueueDiaryEntryInTransaction(
  transaction: IDBTransaction,
  input: { ownerId: string; payload: DiaryCreateCommand; metadata?: CreateMetadata },
): Promise<EnqueueDiaryEntryResult> {
  validateCreate(input.payload);
  requireOwner(await ownerFrom(transaction), input.ownerId);
  return queueCreate(transaction, input.ownerId, structuredClone(input.payload), input.metadata);
}

function applyUpdate(entry: DiaryEntryRecord, payload: DiaryUpdateCommand): DiaryEntryRecord {
  return {
    ...entry,
    ...(Object.hasOwn(payload, "mood_level") ? { mood_level: payload.mood_level ?? null } : {}),
    ...(Object.hasOwn(payload, "mood_words")
      ? { mood_words: [...(payload.mood_words ?? [])] }
      : {}),
    ...(Object.hasOwn(payload, "text") ? { text: payload.text ?? null } : {}),
    ...(Object.hasOwn(payload, "other_medicine_text")
      ? { other_medicine_text: payload.other_medicine_text ?? null }
      : {}),
    revision: entry.revision + 1,
    sync_state: "pending",
  };
}

/** Persist one optimistic edit with its stable ID and expected revision. */
export function enqueueDiaryUpdate(
  database: IDBDatabase,
  input: { ownerId: string; entryId: string; payload: DiaryUpdateCommand },
): Promise<EnqueueDiaryCommandResult> {
  const payload = structuredClone(input.payload);
  if (
    !payload.operation_id ||
    !Number.isInteger(payload.expected_revision) ||
    payload.expected_revision < 1
  ) {
    throw new Error("Diary update requires stable IDs and a positive expected revision");
  }
  if (
    !["mood_level", "mood_words", "text", "other_medicine_text"].some((key) =>
      Object.hasOwn(payload, key),
    )
  ) {
    throw new Error("Diary update must change at least one field");
  }
  return runTransaction(
    database,
    ["meta", "diaryEntries", "diaryOutbox"],
    "readwrite",
    async (transaction) => {
      requireOwner(await ownerFrom(transaction), input.ownerId);
      const replay = await diaryReplayOrConflict(transaction, {
        ownerId: input.ownerId,
        operationId: payload.operation_id,
        entryId: input.entryId,
        action: "update",
        payload,
      });
      if (replay) return replay;
      const key: IDBValidKey = [input.ownerId, input.entryId];
      const entry = await requestResult(
        transaction.objectStore("diaryEntries").get(key) as IDBRequest<
          DiaryEntryRecord | undefined
        >,
      );
      if (!entry || entry.hidden) throw new Error("Visible diary entry is required for an edit");
      if (entry.revision !== payload.expected_revision) {
        throw new Error("Diary entry revision changed before edit");
      }
      const updated = applyUpdate(entry, payload);
      const timestamp = await nextDiaryCommandTime(transaction, input.ownerId, input.entryId);
      const command = diaryCommandRecord(
        input.ownerId,
        input.entryId,
        payload.operation_id,
        "update",
        payload,
        timestamp,
      );
      await Promise.all([
        requestResult(transaction.objectStore("diaryEntries").put(updated)),
        requestResult(transaction.objectStore("diaryOutbox").add(command)),
      ]);
      return { status: "queued", entry: updated, command };
    },
  );
}

/** Persist a hide intent without deleting the entry or its medical history. */
export function enqueueDiaryHide(
  database: IDBDatabase,
  input: { ownerId: string; entryId: string; payload: DiaryHideCommand },
): Promise<EnqueueDiaryCommandResult> {
  const payload = structuredClone(input.payload);
  if (
    !payload.operation_id ||
    !Number.isInteger(payload.expected_revision) ||
    payload.expected_revision < 1
  ) {
    throw new Error("Diary hide requires stable IDs and a positive expected revision");
  }
  return runTransaction(
    database,
    ["meta", "diaryEntries", "diaryOutbox"],
    "readwrite",
    async (transaction) => {
      requireOwner(await ownerFrom(transaction), input.ownerId);
      const replay = await diaryReplayOrConflict(transaction, {
        ownerId: input.ownerId,
        operationId: payload.operation_id,
        entryId: input.entryId,
        action: "hide",
        payload,
      });
      if (replay) return replay;
      const key: IDBValidKey = [input.ownerId, input.entryId];
      const entry = await requestResult(
        transaction.objectStore("diaryEntries").get(key) as IDBRequest<
          DiaryEntryRecord | undefined
        >,
      );
      if (!entry || entry.hidden) throw new Error("Visible diary entry is required for hiding");
      if (entry.revision !== payload.expected_revision) {
        throw new Error("Diary entry revision changed before hiding");
      }
      const hidden = { ...entry, hidden: true, sync_state: "pending" as const };
      const timestamp = await nextDiaryCommandTime(transaction, input.ownerId, input.entryId);
      const command = diaryCommandRecord(
        input.ownerId,
        input.entryId,
        payload.operation_id,
        "hide",
        payload,
        timestamp,
      );
      await Promise.all([
        requestResult(transaction.objectStore("diaryEntries").put(hidden)),
        requestResult(transaction.objectStore("diaryOutbox").add(command)),
      ]);
      return { status: "queued", entry: hidden, command };
    },
  );
}
