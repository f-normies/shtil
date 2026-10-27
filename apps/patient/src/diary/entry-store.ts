import type { Schemas } from "@compass/api-client";
import { requestResult, runTransaction } from "../assessments/database";
import { ownerFrom, requireOwner } from "../assessments/store-owner";
import type { DiaryEntryRecord } from "./merge";
import type { DiaryAudioRecord, DiaryDraftRecord, DiaryOutboxRecord } from "./types";

const MAX_AUDIO_BYTES = 25 * 1024 * 1024;

/** List this owner's visible local and synchronized records by event time. */
export function listDiaryEntries(
  database: IDBDatabase,
  ownerId: string,
): Promise<DiaryEntryRecord[]> {
  return runTransaction(database, ["meta", "diaryEntries"], "readonly", async (transaction) => {
    requireOwner(await ownerFrom(transaction), ownerId);
    const range = IDBKeyRange.bound([ownerId, ""], [ownerId, "\uffff"]);
    const entries = await requestResult(
      transaction.objectStore("diaryEntries").index("owner_occurred").getAll(range) as IDBRequest<
        DiaryEntryRecord[]
      >,
    );
    return entries.filter((entry) => !entry.hidden).reverse();
  });
}

/** Restore the daily completion marker even when its visible entry was soft-hidden. */
export function getDiaryDailyEntry(
  database: IDBDatabase,
  ownerId: string,
  dailyOn: string,
): Promise<DiaryEntryRecord | null> {
  return runTransaction(database, ["meta", "diaryEntries"], "readonly", async (transaction) => {
    requireOwner(await ownerFrom(transaction), ownerId);
    const entry = await requestResult(
      transaction
        .objectStore("diaryEntries")
        .index("owner_daily")
        .get([ownerId, dailyOn]) as IDBRequest<DiaryEntryRecord | undefined>,
    );
    return entry ?? null;
  });
}

/** Return immutable diary commands in creation order for one unlocked owner. */
export function listDiaryOutbox(
  database: IDBDatabase,
  ownerId: string,
): Promise<DiaryOutboxRecord[]> {
  return runTransaction(database, ["meta", "diaryOutbox"], "readonly", async (transaction) => {
    requireOwner(await ownerFrom(transaction), ownerId);
    const range = IDBKeyRange.bound([ownerId, ""], [ownerId, "\uffff"]);
    return requestResult(
      transaction.objectStore("diaryOutbox").index("owner_created").getAll(range) as IDBRequest<
        DiaryOutboxRecord[]
      >,
    );
  });
}

function entrySyncState(commands: readonly DiaryOutboxRecord[]): DiaryEntryRecord["sync_state"] {
  if (commands.some((command) => command.state === "conflict")) return "conflict";
  if (commands.some((command) => command.state === "blocked")) return "blocked";
  return commands.length ? "pending" : "synced";
}

/** Apply a server receipt without replacing a newer optimistic local revision. */
export function confirmDiaryCommand(
  database: IDBDatabase,
  ownerId: string,
  receipt: Schemas["DiaryCommandReceipt"],
): Promise<void> {
  return runTransaction(
    database,
    ["meta", "diaryEntries", "diaryOutbox"],
    "readwrite",
    async (transaction) => {
      requireOwner(await ownerFrom(transaction), ownerId);
      if (!receipt.entry_id || !receipt.operation_id)
        throw new Error("The diary receipt is missing its stable IDs");
      const entries = transaction.objectStore("diaryEntries");
      const outbox = transaction.objectStore("diaryOutbox");
      const [entry, command] = await Promise.all([
        requestResult(
          entries.get([ownerId, receipt.entry_id]) as IDBRequest<DiaryEntryRecord | undefined>,
        ),
        requestResult(
          outbox.get([ownerId, receipt.operation_id]) as IDBRequest<DiaryOutboxRecord | undefined>,
        ),
      ]);
      if (!entry || !command || command.entry_id !== receipt.entry_id) return;
      await requestResult(outbox.delete([ownerId, receipt.operation_id]));
      const range = IDBKeyRange.bound(
        [ownerId, receipt.entry_id, ""],
        [ownerId, receipt.entry_id, "\uffff"],
      );
      const remaining = await requestResult(
        outbox.index("owner_entry_created").getAll(range) as IDBRequest<DiaryOutboxRecord[]>,
      );
      await requestResult(
        entries.put({
          ...entry,
          sync_state: entrySyncState(remaining),
          revision: Math.max(entry.revision, receipt.revision),
          server_revision: Math.max(entry.server_revision ?? 0, receipt.revision),
          received_at: receipt.received_at,
          hidden: Boolean(entry.hidden || receipt.hidden),
        }),
      );
    },
  );
}

/** Preserve local text and a fetched remote version when an immutable command conflicts. */
export function blockDiaryCommand(
  database: IDBDatabase,
  input: {
    ownerId: string;
    operationId: string;
    error: { status: number; code: string };
    remoteSnapshot?: Schemas["DiaryEntryOut"] | null;
  },
): Promise<void> {
  return runTransaction(
    database,
    ["meta", "diaryEntries", "diaryOutbox"],
    "readwrite",
    async (transaction) => {
      requireOwner(await ownerFrom(transaction), input.ownerId);
      const entries = transaction.objectStore("diaryEntries");
      const outbox = transaction.objectStore("diaryOutbox");
      const command = await requestResult(
        outbox.get([input.ownerId, input.operationId]) as IDBRequest<DiaryOutboxRecord | undefined>,
      );
      if (!command) return;
      const conflict = input.error.status === 409;
      await requestResult(
        outbox.put({
          ...command,
          state: conflict ? "conflict" : "blocked",
          last_error: input.error,
        }),
      );
      const entry = await requestResult(
        entries.get([input.ownerId, command.entry_id]) as IDBRequest<DiaryEntryRecord | undefined>,
      );
      if (!entry) return;
      await requestResult(
        entries.put({
          ...entry,
          sync_state: conflict ? "conflict" : "blocked",
          ...(input.remoteSnapshot
            ? { remote_snapshot: structuredClone(input.remoteSnapshot) }
            : {}),
        }),
      );
    },
  );
}

async function digestBlob(blob: Blob): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** Save a completed recorder Blob; callers must stop capture before invoking this method. */
export async function saveDiaryAudio(
  database: IDBDatabase,
  ownerId: string,
  entryId: string,
  blob: Blob,
): Promise<void> {
  if (blob.size === 0 || blob.size > MAX_AUDIO_BYTES)
    throw new Error("Diary audio must contain data and stay within 25 MiB");
  if (!/^audio\/(webm|mp4)(;|$)/i.test(blob.type))
    throw new Error("Diary audio must use WebM/Opus or MP4/AAC");
  const sha256 = await digestBlob(blob);
  await runTransaction(database, ["meta", "diaryAudio"], "readwrite", async (transaction) => {
    requireOwner(await ownerFrom(transaction), ownerId);
    const audioStore = transaction.objectStore("diaryAudio");
    const key: IDBValidKey = [ownerId, entryId];
    const existing = await requestResult(
      audioStore.get(key) as IDBRequest<DiaryAudioRecord | undefined>,
    );
    if (existing && existing.sha256 !== sha256)
      throw new Error("Audio for a diary entry cannot be replaced after it is saved");
    if (!existing)
      await requestResult(
        audioStore.add({
          owner_id: ownerId,
          entry_id: entryId,
          blob,
          sha256,
          saved_at: new Date().toISOString(),
        } satisfies DiaryAudioRecord),
      );
  });
}

/** Read only this owner's completed audio Blob for local playback or upload. */
export function getDiaryAudio(
  database: IDBDatabase,
  ownerId: string,
  entryId: string,
): Promise<Blob | null> {
  return runTransaction(database, ["meta", "diaryAudio"], "readonly", async (transaction) => {
    requireOwner(await ownerFrom(transaction), ownerId);
    const record = await requestResult(
      transaction.objectStore("diaryAudio").get([ownerId, entryId]) as IDBRequest<
        DiaryAudioRecord | undefined
      >,
    );
    return record?.blob ?? null;
  });
}

/** Clear a voice draft only after an explicit mode-switch confirmation. */
export function clearDraftAudio(
  database: IDBDatabase,
  input: { ownerId: string; draftId: string; expectedRevision: number },
): Promise<DiaryDraftRecord> {
  return runTransaction(
    database,
    ["meta", "diaryDrafts", "diaryAudio"],
    "readwrite",
    async (transaction) => {
      requireOwner(await ownerFrom(transaction), input.ownerId);
      const drafts = transaction.objectStore("diaryDrafts");
      const key: IDBValidKey = [input.ownerId, input.draftId];
      const draft = await requestResult(
        drafts.get(key) as IDBRequest<DiaryDraftRecord | undefined>,
      );
      if (!draft || draft.revision !== input.expectedRevision || draft.mode !== "daily") {
        throw new Error("The diary draft changed before its audio could be cleared");
      }
      const updated = {
        ...draft,
        revision: draft.revision + 1,
        updated_at: new Date().toISOString(),
      };
      await Promise.all([
        requestResult(
          transaction.objectStore("diaryAudio").delete([input.ownerId, draft.entry_id]),
        ),
        requestResult(drafts.put(updated)),
      ]);
      return updated;
    },
  );
}
