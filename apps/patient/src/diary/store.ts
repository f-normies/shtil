import type { Schemas } from "@compass/api-client";
import { openAssessmentDatabase, requestResult, runTransaction } from "../assessments/database";
import { ownerFrom, requireOwner } from "../assessments/store-owner";
import {
  cacheDiaryContext,
  cacheDiaryLatestMessage,
  cacheDiaryPrescriptions,
  cacheDiarySchedule,
  getDiaryContextCache,
} from "./context-store";
import {
  blockDiaryCommand,
  clearDraftAudio,
  confirmDiaryCommand,
  getDiaryAudio,
  getDiaryDailyEntry,
  listDiaryEntries,
  listDiaryOutbox,
  saveDiaryAudio,
} from "./entry-store";
import {
  finalizeDailyCheckIn,
  finalizeQuickText,
  updateSavedSurveyProgress,
} from "./finalize-store";
import {
  blockIntakeCommand,
  cacheIntakeMarks,
  confirmIntakeCommand,
  enqueueIntakeMark,
  getCachedIntakeMarks,
  listIntakeOutbox,
} from "./intake-store";
import type { DiaryEntryRecord } from "./merge";
import { enqueueDiaryEntry, enqueueDiaryHide, enqueueDiaryUpdate } from "./outbox-commands";
import { enqueueDiaryVoiceEntry } from "./outbox-voice";
import type {
  DiaryContextCacheRecord,
  DiaryCreateCommand,
  DiaryDraftInput,
  DiaryDraftRecord,
  DiaryHideCommand,
  DiaryOutboxRecord,
  DiaryUpdateCommand,
  DiaryVoiceCommand,
  EnqueueDiaryCommandResult,
  EnqueueDiaryEntryResult,
  EnqueueIntakeMarkResult,
  FinalizeDailyCheckInInput,
  FinalizeDailyCheckInResult,
  FinalizeQuickTextInput,
  FinalizeQuickTextResult,
  IntakeCommand,
  IntakeOutboxRecord,
  SaveDiaryDraftResult,
} from "./types";

function updatedAt(previous: string | undefined): string {
  const priorTime = previous ? Date.parse(previous) : Number.NEGATIVE_INFINITY;
  return new Date(Math.max(Date.now(), priorTime + 1)).toISOString();
}

function draftFromInput(
  input: DiaryDraftInput,
  current: DiaryDraftRecord | undefined,
): DiaryDraftRecord {
  const draft: DiaryDraftRecord = {
    owner_id: input.ownerId,
    draft_id: input.draftId,
    entry_id: current?.entry_id ?? input.entryId ?? crypto.randomUUID(),
    operation_id: current?.operation_id ?? input.operationId ?? crypto.randomUUID(),
    mode: input.mode,
    entry_kind: input.entryKind,
    occurred_at: input.occurredAt,
    step: input.step,
    mood_level: input.moodLevel,
    mood_words: [...input.moodWords],
    text: input.text,
    other_medicine_text:
      input.otherMedicineText === undefined
        ? (current?.other_medicine_text ?? null)
        : input.otherMedicineText,
    schedule_snapshot:
      input.scheduleSnapshot === undefined
        ? (current?.schedule_snapshot ?? null)
        : structuredClone(input.scheduleSnapshot),
    schedule_needs_refresh: input.scheduleNeedsRefresh ?? current?.schedule_needs_refresh ?? false,
    survey_status: input.mode === "daily" ? input.surveyStatus : "unknown",
    survey_submission_ids: input.surveySubmissionIds
      ? [...input.surveySubmissionIds]
      : (current?.survey_submission_ids ?? []),
    revision: (current?.revision ?? 0) + 1,
    updated_at: updatedAt(current?.updated_at),
  };
  if (input.mode === "daily") draft.daily_on = input.dailyOn;
  return draft;
}

function validateInput(input: DiaryDraftInput): void {
  if (!input.ownerId || !input.draftId) throw new Error("Diary draft identity is required");
  if (
    (input.entryId !== undefined && !input.entryId) ||
    (input.operationId !== undefined && !input.operationId)
  ) {
    throw new Error("Diary entry and operation IDs must be non-empty when provided");
  }
  if (!Number.isFinite(Date.parse(input.occurredAt))) {
    throw new Error("Diary draft event time must be valid");
  }
  if (input.mode === "daily") {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(input.dailyOn)) {
      throw new Error("Daily diary date must use YYYY-MM-DD");
    }
    if (!Number.isInteger(input.moodLevel) || input.moodLevel < 1 || input.moodLevel > 5) {
      throw new Error("A daily diary draft requires a mood level from 1 to 5");
    }
  }
}

/** Durable, owner-bound local drafts for daily and quick diary flows. */
export class DiaryStore {
  private constructor(private readonly database: IDBDatabase) {}

  /** Open the shared assessment database after the diary-store upgrade. */
  static async open(): Promise<DiaryStore> {
    return new DiaryStore(await openAssessmentDatabase());
  }

  /** Save a draft revision while preserving the flow's entry and command IDs. */
  async saveDraft(input: DiaryDraftInput): Promise<SaveDiaryDraftResult> {
    validateInput(input);
    return runTransaction(
      this.database,
      ["meta", "diaryDrafts"],
      "readwrite",
      async (transaction) => {
        requireOwner(await ownerFrom(transaction), input.ownerId);
        const drafts = transaction.objectStore("diaryDrafts");
        const key: IDBValidKey = [input.ownerId, input.draftId];
        const current = await requestResult(
          drafts.get(key) as IDBRequest<DiaryDraftRecord | undefined>,
        );
        if ((current?.revision ?? 0) !== input.expectedRevision) {
          return { status: "conflict", currentDraft: current ?? null };
        }
        if (
          current &&
          (current.mode !== input.mode ||
            current.daily_on !== input.dailyOn ||
            (input.entryId !== undefined && current.entry_id !== input.entryId) ||
            (input.operationId !== undefined && current.operation_id !== input.operationId))
        ) {
          return { status: "conflict", currentDraft: current };
        }
        if (input.mode === "daily") {
          const daily = await requestResult(
            drafts.index("owner_daily").get([input.ownerId, input.dailyOn]) as IDBRequest<
              DiaryDraftRecord | undefined
            >,
          );
          if (daily && daily.draft_id !== input.draftId) {
            return { status: "daily-exists", currentDraft: daily };
          }
        }
        const draft = draftFromInput(input, current);
        await requestResult(drafts.put(draft));
        return { status: "saved", draft };
      },
    );
  }

  /** Restore one draft only when the shared owner pointer still grants access. */
  async getDraft(ownerId: string, draftId: string): Promise<DiaryDraftRecord | null> {
    return runTransaction(
      this.database,
      ["meta", "diaryDrafts"],
      "readonly",
      async (transaction) => {
        requireOwner(await ownerFrom(transaction), ownerId);
        const draft = await requestResult(
          transaction.objectStore("diaryDrafts").get([ownerId, draftId]) as IDBRequest<
            DiaryDraftRecord | undefined
          >,
        );
        return draft ?? null;
      },
    );
  }

  /** Restore all resumable drafts for this patient, newest update first. */
  async listDrafts(ownerId: string): Promise<DiaryDraftRecord[]> {
    return runTransaction(
      this.database,
      ["meta", "diaryDrafts"],
      "readonly",
      async (transaction) => {
        requireOwner(await ownerFrom(transaction), ownerId);
        const range = IDBKeyRange.bound([ownerId, ""], [ownerId, "\uffff"]);
        const drafts = await requestResult(
          transaction.objectStore("diaryDrafts").index("owner_updated").getAll(range) as IDBRequest<
            DiaryDraftRecord[]
          >,
        );
        return drafts.reverse();
      },
    );
  }

  /** Atomically save a visible local entry and its stable API command. */
  async enqueueEntry(input: {
    ownerId: string;
    payload: DiaryCreateCommand;
  }): Promise<EnqueueDiaryEntryResult> {
    return enqueueDiaryEntry(this.database, input);
  }

  /** Queue an optimistic edit with its stable operation ID and expected server revision. */
  async enqueueUpdate(input: {
    ownerId: string;
    entryId: string;
    payload: DiaryUpdateCommand;
  }): Promise<EnqueueDiaryCommandResult> {
    return enqueueDiaryUpdate(this.database, input);
  }

  /** Queue a durable hide command while retaining the entry and its local history. */
  async enqueueHide(input: {
    ownerId: string;
    entryId: string;
    payload: DiaryHideCommand;
  }): Promise<EnqueueDiaryCommandResult> {
    return enqueueDiaryHide(this.database, input);
  }

  /** Atomically retain a voice recording and queue its owner-bound multipart command. */
  async enqueueVoiceEntry(input: {
    ownerId: string;
    payload: DiaryVoiceCommand;
    blob: Blob;
  }): Promise<EnqueueDiaryCommandResult> {
    return enqueueDiaryVoiceEntry(this.database, input);
  }

  /** Atomically save a daily entry and any assessment drafts not yet durably queued. */
  async finalizeDailyCheckIn(
    input: FinalizeDailyCheckInInput,
  ): Promise<FinalizeDailyCheckInResult> {
    return finalizeDailyCheckIn(this.database, input);
  }

  /** Atomically queue a quick text entry and remove the matching local draft. */
  async finalizeQuickText(input: FinalizeQuickTextInput): Promise<FinalizeQuickTextResult> {
    return finalizeQuickText(this.database, input);
  }

  /** Update only local survey progress; the immutable diary API command stays unchanged. */
  async updateSavedSurveyProgress(input: {
    ownerId: string;
    entryId: string;
    status: "queued" | "completed" | "not_due";
    submissionIds: string[];
  }): Promise<void> {
    return updateSavedSurveyProgress(this.database, input);
  }

  /** Read visible local entries for the current patient. */
  async listEntries(ownerId: string): Promise<DiaryEntryRecord[]> {
    return listDiaryEntries(this.database, ownerId);
  }

  /** Read the completion marker independently from the visible diary list. */
  async getDailyEntry(ownerId: string, dailyOn: string): Promise<DiaryEntryRecord | null> {
    return getDiaryDailyEntry(this.database, ownerId, dailyOn);
  }

  /** Read pending, blocked, or conflicted diary commands for one owner. */
  async getOutboxForOwner(ownerId: string): Promise<DiaryOutboxRecord[]> {
    return listDiaryOutbox(this.database, ownerId);
  }

  /** Queue one explicit dose mark under its immutable operation ID. */
  async enqueueIntakeMark(input: {
    ownerId: string;
    payload: IntakeCommand;
  }): Promise<EnqueueIntakeMarkResult> {
    return enqueueIntakeMark(this.database, input);
  }

  /** Read queued dose marks in creation order for this owner. */
  async getIntakeOutboxForOwner(ownerId: string): Promise<IntakeOutboxRecord[]> {
    return listIntakeOutbox(this.database, ownerId);
  }

  /** Commit one accepted server mark and remove only its matching command. */
  async confirmIntakeCommand(input: {
    ownerId: string;
    operationId: string;
    mark: Schemas["IntakeMarkOut"];
  }): Promise<void> {
    return confirmIntakeCommand(this.database, input);
  }

  /** Retain a rejected explicit mark for a visible recovery decision. */
  async blockIntakeCommand(input: {
    ownerId: string;
    operationId: string;
    error: { status: number; code: string };
  }): Promise<void> {
    return blockIntakeCommand(this.database, input);
  }

  /** Cache a successful daily intake response, replacing only that owner's day. */
  async cacheIntakeMarks(
    ownerId: string,
    scheduledOn: string,
    marks: readonly Schemas["IntakeMarkOut"][],
  ): Promise<void> {
    return cacheIntakeMarks(this.database, ownerId, scheduledOn, marks);
  }

  /** Restore the last successfully fetched marks for one day. */
  async getCachedIntakeMarks(
    ownerId: string,
    scheduledOn: string,
  ): Promise<Schemas["IntakeMarkOut"][]> {
    return getCachedIntakeMarks(this.database, ownerId, scheduledOn);
  }

  /** Commit one API receipt to local state and remove only its matching command. */
  async confirmCommand(ownerId: string, receipt: Schemas["DiaryCommandReceipt"]): Promise<void> {
    return confirmDiaryCommand(this.database, ownerId, receipt);
  }

  /** Keep the immutable command and local version when the server rejects it. */
  async blockCommand(input: {
    ownerId: string;
    operationId: string;
    error: { status: number; code: string };
    remoteSnapshot?: Schemas["DiaryEntryOut"] | null;
  }): Promise<void> {
    return blockDiaryCommand(this.database, input);
  }

  /** Persist the finalized recorder Blob after recording has stopped. */
  async saveAudioBlob(ownerId: string, entryId: string, blob: Blob): Promise<void> {
    return saveDiaryAudio(this.database, ownerId, entryId, blob);
  }

  /** Clear saved audio and advance its daily draft only after confirmed mode change. */
  async clearDraftAudio(input: {
    ownerId: string;
    draftId: string;
    expectedRevision: number;
  }): Promise<DiaryDraftRecord> {
    return clearDraftAudio(this.database, input);
  }

  /** Restore completed audio for playback or a pending upload. */
  async getAudioBlob(ownerId: string, entryId: string): Promise<Blob | null> {
    return getDiaryAudio(this.database, ownerId, entryId);
  }

  /** Cache a successful diary context response for offline day and count recovery. */
  async cacheContext(
    ownerId: string,
    context: Schemas["DiaryContextOut"],
  ): Promise<DiaryContextCacheRecord> {
    return cacheDiaryContext(this.database, ownerId, context);
  }

  /** Cache a successful current schedule without deriving dates on the client. */
  async cacheSchedule(
    ownerId: string,
    schedule: Schemas["ScheduleOut"],
  ): Promise<DiaryContextCacheRecord> {
    return cacheDiarySchedule(this.database, ownerId, schedule);
  }

  /** Cache successful current prescription data for offline check-in restoration. */
  async cachePrescriptions(
    ownerId: string,
    prescriptions: readonly Schemas["ScheduleItem"][],
  ): Promise<DiaryContextCacheRecord> {
    return cacheDiaryPrescriptions(this.database, ownerId, prescriptions);
  }

  /** Cache the latest successfully fetched visible message, including an empty result. */
  async cacheLatestMessage(
    ownerId: string,
    message: Schemas["MessageOut"] | null,
  ): Promise<DiaryContextCacheRecord> {
    return cacheDiaryLatestMessage(this.database, ownerId, message);
  }

  /** Read the last successful context and schedule for offline continuation. */
  async getContextCache(ownerId: string): Promise<DiaryContextCacheRecord | null> {
    return getDiaryContextCache(this.database, ownerId);
  }
}
