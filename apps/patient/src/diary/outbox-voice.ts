import { requestResult, runTransaction } from "../assessments/database";
import { ownerFrom, requireOwner } from "../assessments/store-owner";
import type { DiaryEntryRecord } from "./merge";
import {
  addDiaryEntryCommand,
  diaryEntryFromCommand,
  diaryReplayOrConflict,
  digestDiaryAudio,
  MAX_DIARY_AUDIO_BYTES,
} from "./outbox-command-helpers";
import type {
  DiaryAudioRecord,
  DiaryScheduleSnapshot,
  DiarySurveyStatus,
  DiaryVoiceCommand,
  EnqueueDiaryCommandResult,
  EnqueueDiaryEntryResult,
} from "./types";

export type PreparedDiaryVoiceCommand = {
  payload: DiaryVoiceCommand;
  blob: Blob;
  digest: string;
};

type EnqueueDiaryVoiceTransactionResult = EnqueueDiaryCommandResult | EnqueueDiaryEntryResult;

type DiaryEntryMetadata = {
  surveyStatus?: DiarySurveyStatus;
  surveySubmissionIds?: string[];
  scheduleSnapshot?: DiaryScheduleSnapshot | null;
};

/** Validate audio and compute its hash before opening any write transaction. */
export async function prepareDiaryVoiceCommand(
  payloadInput: DiaryVoiceCommand,
  blob: Blob,
): Promise<PreparedDiaryVoiceCommand> {
  if (blob.size === 0 || blob.size > MAX_DIARY_AUDIO_BYTES) {
    throw new Error("Diary audio must stay within 25 MiB");
  }
  if (!/^audio\/(webm|mp4)(;|$)/i.test(blob.type)) {
    throw new Error("Diary audio must use WebM or MP4");
  }
  const payload: DiaryVoiceCommand = {
    ...structuredClone(payloadInput),
    daily_on: payloadInput.daily_on ?? null,
    mood_level: payloadInput.mood_level ?? null,
    other_medicine_text: payloadInput.other_medicine_text ?? null,
    mood_words: [...(payloadInput.mood_words ?? [])],
  };
  if (
    !payload.operation_id ||
    !payload.entry_id ||
    !Number.isInteger(payload.duration_ms) ||
    payload.duration_ms < 1 ||
    payload.duration_ms > 600_000
  ) {
    throw new Error("Voice command requires stable IDs and a valid duration");
  }
  if (!Number.isFinite(Date.parse(payload.occurred_at))) {
    throw new Error("Diary event time must be valid");
  }
  if ((payload.kind === "daily") !== (payload.daily_on !== null)) {
    throw new Error("Daily voice entries require a date");
  }
  if (payload.kind === "daily" && payload.mood_level === null) {
    throw new Error("Daily voice entries require a mood level");
  }
  const digest = await digestDiaryAudio(blob);
  return { payload, blob, digest };
}

/** Add voice entry, outbox command, and Blob to the caller's existing transaction. */
export async function enqueueDiaryVoiceEntryInTransaction(
  transaction: IDBTransaction,
  input: { ownerId: string; prepared: PreparedDiaryVoiceCommand; metadata?: DiaryEntryMetadata },
): Promise<EnqueueDiaryVoiceTransactionResult> {
  const { payload, blob, digest } = input.prepared;
  const metadata = input.metadata;
  const replay = await diaryReplayOrConflict(transaction, {
    ownerId: input.ownerId,
    operationId: payload.operation_id,
    entryId: payload.entry_id,
    action: "voice",
    payload,
    audioSha256: digest,
  });
  if (replay) return replay;

  const audioStore = transaction.objectStore("diaryAudio");
  const audioKey: IDBValidKey = [input.ownerId, payload.entry_id];
  const existingAudio = await requestResult(
    audioStore.get(audioKey) as IDBRequest<DiaryAudioRecord | undefined>,
  );
  if (existingAudio && existingAudio.sha256 !== digest) {
    throw new Error("Audio for a diary entry cannot be replaced after it is saved");
  }
  if (payload.kind === "daily" && payload.daily_on) {
    const daily = await requestResult(
      transaction
        .objectStore("diaryEntries")
        .index("owner_daily")
        .get([input.ownerId, payload.daily_on]) as IDBRequest<DiaryEntryRecord | undefined>,
    );
    if (daily) return { status: "daily-exists", currentEntry: daily };
  }
  const result = await addDiaryEntryCommand(transaction, {
    ownerId: input.ownerId,
    entry: diaryEntryFromCommand(input.ownerId, payload, metadata),
    action: "voice",
    payload,
    audioSha256: digest,
  });
  if (!existingAudio) {
    await requestResult(
      audioStore.add({
        owner_id: input.ownerId,
        entry_id: payload.entry_id,
        blob,
        sha256: digest,
        saved_at: new Date().toISOString(),
      } satisfies DiaryAudioRecord),
    );
  }
  return result;
}

/** Atomically save a voice entry, its immutable upload, and its owner-bound Blob. */
export async function enqueueDiaryVoiceEntry(
  database: IDBDatabase,
  input: { ownerId: string; payload: DiaryVoiceCommand; blob: Blob },
): Promise<EnqueueDiaryCommandResult> {
  const prepared = await prepareDiaryVoiceCommand(input.payload, input.blob);
  return runTransaction(
    database,
    ["meta", "diaryEntries", "diaryOutbox", "diaryAudio"],
    "readwrite",
    async (transaction) => {
      requireOwner(await ownerFrom(transaction), input.ownerId);
      const result = await enqueueDiaryVoiceEntryInTransaction(transaction, {
        ownerId: input.ownerId,
        prepared,
      });
      if (result.status === "daily-exists") {
        throw new Error("A daily entry already exists for this date");
      }
      return result;
    },
  );
}
