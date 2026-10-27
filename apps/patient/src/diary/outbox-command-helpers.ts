import { requestResult } from "../assessments/database";
import type { DiaryEntryRecord } from "./merge";
import type {
  DiaryCreateCommand,
  DiaryHideCommand,
  DiaryOutboxAction,
  DiaryOutboxPayload,
  DiaryOutboxRecord,
  DiaryScheduleSnapshot,
  DiarySurveyStatus,
  DiaryUpdateCommand,
  DiaryVoiceCommand,
  EnqueueDiaryCommandResult,
} from "./types";

export const MAX_DIARY_AUDIO_BYTES = 25 * 1024 * 1024;

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    const entries = Object.entries(value).sort(([left], [right]) => left.localeCompare(right));
    return `{${entries.map(([key, nested]) => `${JSON.stringify(key)}:${canonicalJson(nested)}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export function diaryEntryFromCommand(
  ownerId: string,
  payload: DiaryCreateCommand | DiaryVoiceCommand,
  metadata?: {
    surveyStatus?: DiarySurveyStatus;
    surveySubmissionIds?: string[];
    scheduleSnapshot?: DiaryScheduleSnapshot | null;
  },
): DiaryEntryRecord {
  return {
    owner_id: ownerId,
    entry_id: payload.entry_id,
    operation_id: payload.operation_id,
    kind: payload.kind,
    occurred_at: payload.occurred_at,
    daily_on: payload.daily_on ?? null,
    mood_level: payload.mood_level ?? null,
    mood_words: [...(payload.mood_words ?? [])],
    text: "text" in payload ? (payload.text ?? null) : null,
    other_medicine_text: payload.other_medicine_text ?? null,
    audio: null,
    revision: 1,
    server_revision: 0,
    sync_state: "pending",
    remote_snapshot: null,
    received_at: null,
    source_statuses: [],
    survey_status: metadata?.surveyStatus,
    survey_submission_ids: metadata?.surveySubmissionIds ? [...metadata.surveySubmissionIds] : [],
    schedule_snapshot: metadata?.scheduleSnapshot
      ? structuredClone(metadata.scheduleSnapshot)
      : null,
    hidden: false,
  };
}

export async function nextDiaryCommandTime(
  transaction: IDBTransaction,
  ownerId: string,
  entryId: string,
): Promise<string> {
  const range = IDBKeyRange.bound([ownerId, entryId, ""], [ownerId, entryId, "\uffff"]);
  const previous = await requestResult(
    transaction.objectStore("diaryOutbox").index("owner_entry_created").getAll(range) as IDBRequest<
      DiaryOutboxRecord[]
    >,
  );
  const lastTimestamp = previous.reduce(
    (latest, command) => Math.max(latest, Date.parse(command.created_at)),
    Number.NEGATIVE_INFINITY,
  );
  return new Date(Math.max(Date.now(), lastTimestamp + 1)).toISOString();
}

export function diaryCommandRecord(
  ownerId: string,
  entryId: string,
  operationId: string,
  action: DiaryOutboxAction,
  payload: DiaryOutboxPayload,
  createdAt: string,
  audioSha256?: string,
): DiaryOutboxRecord {
  const base = {
    owner_id: ownerId,
    operation_id: operationId,
    entry_id: entryId,
    state: "pending" as const,
    last_error: null,
    payload_conflicts: [],
    created_at: createdAt,
  };
  if (action === "create") return { ...base, action, payload: payload as DiaryCreateCommand };
  if (action === "update") return { ...base, action, payload: payload as DiaryUpdateCommand };
  if (action === "hide") return { ...base, action, payload: payload as DiaryHideCommand };
  return {
    ...base,
    action,
    payload: payload as DiaryVoiceCommand,
    audio_sha256: audioSha256 ?? "",
  };
}

export async function diaryReplayOrConflict(
  transaction: IDBTransaction,
  input: {
    ownerId: string;
    operationId: string;
    entryId: string;
    action: DiaryOutboxAction;
    payload: DiaryOutboxPayload;
    audioSha256?: string;
  },
): Promise<EnqueueDiaryCommandResult | null> {
  const outbox = transaction.objectStore("diaryOutbox");
  const existing = await requestResult(
    outbox.get([input.ownerId, input.operationId]) as IDBRequest<DiaryOutboxRecord | undefined>,
  );
  if (!existing) return null;
  const sameBody =
    existing.action === input.action &&
    existing.entry_id === input.entryId &&
    canonicalJson(existing.payload) === canonicalJson(input.payload);
  const sameAudio = existing.action !== "voice" || existing.audio_sha256 === input.audioSha256;
  if (sameBody && sameAudio) {
    const entry = await requestResult(
      transaction.objectStore("diaryEntries").get([input.ownerId, input.entryId]) as IDBRequest<
        DiaryEntryRecord | undefined
      >,
    );
    if (!entry) throw new Error("Queued diary operation has no local entry");
    return { status: "replayed", entry, command: existing };
  }

  const conflicts = existing.payload_conflicts ?? [];
  const alreadyStored = conflicts.some(
    (candidate) => canonicalJson(candidate) === canonicalJson(input.payload),
  );
  const updated = {
    ...existing,
    state: "conflict" as const,
    last_error: { status: 409, code: "local_operation_conflict" },
    payload_conflicts: alreadyStored ? conflicts : [...conflicts, structuredClone(input.payload)],
    ...(input.action === "voice" && existing.action === "voice"
      ? { conflicting_audio_sha256: input.audioSha256 }
      : {}),
  } as DiaryOutboxRecord;
  await requestResult(outbox.put(updated));
  const currentEntry = await requestResult(
    transaction.objectStore("diaryEntries").get([input.ownerId, existing.entry_id]) as IDBRequest<
      DiaryEntryRecord | undefined
    >,
  );
  if (currentEntry) {
    await requestResult(
      transaction.objectStore("diaryEntries").put({ ...currentEntry, sync_state: "conflict" }),
    );
  }
  return { status: "conflict", command: updated };
}

export async function addDiaryEntryCommand(
  transaction: IDBTransaction,
  input: {
    ownerId: string;
    entry: DiaryEntryRecord;
    action: DiaryOutboxAction;
    payload: DiaryOutboxPayload;
    audioSha256?: string;
  },
): Promise<EnqueueDiaryCommandResult> {
  const key: IDBValidKey = [input.ownerId, input.entry.entry_id];
  const current = await requestResult(
    transaction.objectStore("diaryEntries").get(key) as IDBRequest<DiaryEntryRecord | undefined>,
  );
  if (current) throw new Error("A diary entry ID cannot be reused with a different operation");
  const timestamp = await nextDiaryCommandTime(transaction, input.ownerId, input.entry.entry_id);
  const command = diaryCommandRecord(
    input.ownerId,
    input.entry.entry_id,
    input.payload.operation_id,
    input.action,
    input.payload,
    timestamp,
    input.audioSha256,
  );
  await Promise.all([
    requestResult(transaction.objectStore("diaryEntries").add(input.entry)),
    requestResult(transaction.objectStore("diaryOutbox").add(command)),
  ]);
  return { status: "queued", entry: input.entry, command };
}

export async function digestDiaryAudio(blob: Blob): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
