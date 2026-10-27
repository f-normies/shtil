import { requestResult, runTransaction } from "../assessments/database";
import { enqueueAssessmentDraftInTransaction } from "../assessments/enqueue-draft-transaction";
import { ownerFrom, requireOwner } from "../assessments/store-owner";
import { enqueueDiaryEntryInTransaction } from "./outbox-commands";
import {
  enqueueDiaryVoiceEntryInTransaction,
  type PreparedDiaryVoiceCommand,
  prepareDiaryVoiceCommand,
} from "./outbox-voice";
import type {
  DiaryDraftRecord,
  DiaryEntryRecord,
  DiaryVoiceCommand,
  FinalizeDailyCheckInInput,
  FinalizeDailyCheckInResult,
  FinalizeQuickTextInput,
  FinalizeQuickTextResult,
} from "./types";

function sameJson(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function assertDraftMatchesPayload(
  draft: DiaryDraftRecord,
  input: FinalizeDailyCheckInInput,
): void {
  const { payload } = input;
  const text = "text" in payload ? (payload.text ?? "") : "";
  if (
    draft.mode !== "daily" ||
    draft.entry_kind !== "daily" ||
    payload.kind !== "daily" ||
    draft.entry_id !== payload.entry_id ||
    draft.operation_id !== payload.operation_id ||
    draft.daily_on !== payload.daily_on ||
    draft.occurred_at !== payload.occurred_at ||
    draft.mood_level !== payload.mood_level ||
    !sameJson(draft.mood_words, payload.mood_words ?? []) ||
    draft.text !== text ||
    (draft.other_medicine_text ?? null) !== (payload.other_medicine_text ?? null)
  ) {
    throw new Error("Daily entry payload must match the saved draft and stable operation IDs");
  }
}

function assertQuickTextDraftMatchesPayload(
  input: FinalizeQuickTextInput,
  draft: DiaryDraftRecord,
): void {
  const { payload } = input;
  if (
    draft.mode !== "quick" ||
    draft.entry_kind !== "text" ||
    payload.kind !== "text" ||
    draft.entry_id !== payload.entry_id ||
    draft.operation_id !== payload.operation_id ||
    draft.daily_on !== undefined ||
    payload.daily_on !== null ||
    draft.occurred_at !== payload.occurred_at ||
    draft.mood_level !== payload.mood_level ||
    !sameJson(draft.mood_words, payload.mood_words ?? []) ||
    draft.text !== (payload.text ?? "") ||
    (draft.other_medicine_text ?? null) !== (payload.other_medicine_text ?? null)
  ) {
    throw new Error("Quick text payload must match the saved draft and stable operation IDs");
  }
}

/** Commit a quick text entry and remove its draft in the same durable transaction. */
export async function finalizeQuickText(
  database: IDBDatabase,
  input: FinalizeQuickTextInput,
): Promise<FinalizeQuickTextResult> {
  return runTransaction(
    database,
    ["meta", "diaryDrafts", "diaryEntries", "diaryOutbox"],
    "readwrite",
    async (transaction) => {
      requireOwner(await ownerFrom(transaction), input.ownerId);
      const drafts = transaction.objectStore("diaryDrafts");
      const key: IDBValidKey = [input.ownerId, input.draftId];
      const draft = await requestResult(
        drafts.get(key) as IDBRequest<DiaryDraftRecord | undefined>,
      );
      if (!draft || draft.revision !== input.expectedDraftRevision) {
        return { status: "draft-conflict", currentDraft: draft ?? null };
      }
      assertQuickTextDraftMatchesPayload(input, draft);

      const entryResult = await enqueueDiaryEntryInTransaction(transaction, {
        ownerId: input.ownerId,
        payload: input.payload,
      });
      if (entryResult.status === "conflict" || entryResult.status === "daily-exists") {
        return entryResult;
      }
      await requestResult(drafts.delete(key));
      return entryResult;
    },
  );
}

function validateAssessmentDrafts(input: FinalizeDailyCheckInInput): void {
  const surveyIds = new Set(input.surveySubmissionIds);
  if (surveyIds.size !== input.surveySubmissionIds.length) {
    throw new Error("Survey submission IDs must be unique");
  }
  const scaleIds = new Set<string>();
  for (const draft of input.assessmentDrafts) {
    if (draft.ownerId !== input.ownerId || !surveyIds.has(draft.payload.submission_id)) {
      throw new Error("Assessment drafts must belong to the owner and saved survey receipts");
    }
    if (scaleIds.has(draft.scaleId)) throw new Error("A survey can queue only one draft per scale");
    scaleIds.add(draft.scaleId);
  }
}

/** Commit a daily diary entry and any unqueued survey drafts as one durable action. */
export async function finalizeDailyCheckIn(
  database: IDBDatabase,
  input: FinalizeDailyCheckInInput,
): Promise<FinalizeDailyCheckInResult> {
  validateAssessmentDrafts(input);
  let preparedVoice: PreparedDiaryVoiceCommand | null = null;
  if ("duration_ms" in input.payload) {
    if (!input.voiceBlob) throw new Error("A daily voice command requires its saved audio Blob");
    preparedVoice = await prepareDiaryVoiceCommand(
      input.payload as DiaryVoiceCommand,
      input.voiceBlob,
    );
  } else if (input.voiceBlob) {
    throw new Error("A text daily command cannot include an audio Blob");
  }
  const stores = ["meta", "diaryDrafts", "diaryEntries", "diaryOutbox", "drafts", "outbox"];
  if ("duration_ms" in input.payload) stores.push("diaryAudio");
  return runTransaction(database, stores, "readwrite", async (transaction) => {
    requireOwner(await ownerFrom(transaction), input.ownerId);
    const drafts = transaction.objectStore("diaryDrafts");
    const key: IDBValidKey = [input.ownerId, input.draftId];
    const draft = await requestResult(drafts.get(key) as IDBRequest<DiaryDraftRecord | undefined>);
    if (!draft || draft.revision !== input.expectedDraftRevision) {
      return { status: "draft-conflict", currentDraft: draft ?? null };
    }
    assertDraftMatchesPayload(draft, input);

    const metadata = {
      surveyStatus: input.surveyStatus,
      surveySubmissionIds: input.surveySubmissionIds,
      scheduleSnapshot: draft.schedule_snapshot,
    };
    const entryResult =
      "duration_ms" in input.payload
        ? await (() => {
            if (!preparedVoice) throw new Error("Daily voice audio was not prepared");
            return enqueueDiaryVoiceEntryInTransaction(transaction, {
              ownerId: input.ownerId,
              prepared: preparedVoice,
              metadata,
            });
          })()
        : await enqueueDiaryEntryInTransaction(transaction, {
            ownerId: input.ownerId,
            payload: input.payload,
            metadata,
          });
    if (entryResult.status === "daily-exists" || entryResult.status === "conflict") {
      return entryResult;
    }

    for (const assessmentDraft of input.assessmentDrafts) {
      const queued = await enqueueAssessmentDraftInTransaction(transaction, assessmentDraft);
      if (queued.status === "conflict") {
        throw new Error("Assessment draft changed while finalizing the daily check-in");
      }
    }

    if (
      input.surveyStatus === "queued" ||
      input.surveyStatus === "completed" ||
      input.surveyStatus === "not_due"
    ) {
      await requestResult(drafts.delete(key));
    } else {
      await requestResult(
        drafts.put({
          ...draft,
          survey_status: input.surveyStatus,
          survey_submission_ids: [...input.surveySubmissionIds],
          step: "survey",
          revision: draft.revision + 1,
          updated_at: new Date().toISOString(),
        } satisfies DiaryDraftRecord),
      );
    }
    if (
      input.surveyStatus === "queued" ||
      input.surveyStatus === "completed" ||
      input.surveyStatus === "not_due"
    ) {
      await updateEntrySurveyProjection(transaction, input);
    }
    return entryResult;
  });
}

/** Save survey-only progress without replacing the immutable diary create command. */
export function updateSavedSurveyProgress(
  database: IDBDatabase,
  input: {
    ownerId: string;
    entryId: string;
    status: "queued" | "completed" | "not_due";
    submissionIds: string[];
  },
): Promise<void> {
  if (new Set(input.submissionIds).size !== input.submissionIds.length) {
    throw new Error("Survey submission IDs must be unique");
  }
  return runTransaction(database, ["meta", "diaryEntries"], "readwrite", async (transaction) => {
    requireOwner(await ownerFrom(transaction), input.ownerId);
    const entries = transaction.objectStore("diaryEntries");
    const key: IDBValidKey = [input.ownerId, input.entryId];
    const entry = await requestResult(entries.get(key) as IDBRequest<DiaryEntryRecord | undefined>);
    if (!entry) return;
    await requestResult(
      entries.put({
        ...entry,
        survey_status: input.status,
        survey_submission_ids: [...input.submissionIds],
      }),
    );
  });
}

async function updateEntrySurveyProjection(
  transaction: IDBTransaction,
  input: FinalizeDailyCheckInInput,
): Promise<void> {
  const entries = transaction.objectStore("diaryEntries");
  const key: IDBValidKey = [input.ownerId, input.payload.entry_id];
  const entry = await requestResult(entries.get(key) as IDBRequest<DiaryEntryRecord | undefined>);
  if (!entry) return;
  await requestResult(
    entries.put({
      ...entry,
      survey_status: input.surveyStatus,
      survey_submission_ids: [...input.surveySubmissionIds],
    }),
  );
}
