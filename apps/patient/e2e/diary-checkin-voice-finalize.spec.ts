import { expect, test } from "@playwright/test";
import { DEFINITION_V1, PATIENT_A, payload, SCALE_ID, SUBMISSION_A } from "./assessment-fixtures";
import { mockAssessmentAuth, openPatientPage } from "./assessment-test-helpers";

const DAILY_DRAFT_ID = "00000000-0000-4000-8000-0000000000e1";
const DAILY_ENTRY_ID = "00000000-0000-4000-8000-0000000000e2";
const DAILY_OPERATION_ID = "00000000-0000-4000-8000-0000000000e3";

test.beforeEach(async ({ context }) => mockAssessmentAuth(context));

async function prepareVoiceDraft(page: import("@playwright/test").Page): Promise<void> {
  await openPatientPage(page);
  await page.evaluate(
    async ({
      ownerId,
      definition,
      scaleId,
      submissionId,
      dailyDraftId,
      dailyEntryId,
      operationId,
    }) => {
      const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
      const { DiaryStore } = await import("/patient/src/diary/store.ts");
      const assessments = await AssessmentStore.open();
      await assessments.rememberOnlinePatient({ id: ownerId, role: "patient" });
      await assessments.saveDraft({
        ownerId,
        scaleId,
        expectedRevision: 0,
        answers: { q1: 0 },
        definitionSnapshot: definition,
      });
      const diary = await DiaryStore.open();
      await diary.saveDraft({
        ownerId,
        draftId: dailyDraftId,
        entryId: dailyEntryId,
        operationId,
        expectedRevision: 0,
        mode: "daily",
        entryKind: "daily",
        dailyOn: "2026-10-23",
        occurredAt: "2026-10-23T10:00:00.000Z",
        step: "survey",
        moodLevel: 3,
        moodWords: [],
        text: "",
        scheduleSnapshot: {
          today: "2026-10-23",
          items: [{ scale_id: scaleId, scale_title: "Synthetic", due_on: "2026-10-23" }],
          fetched_at: "2026-10-23T09:00:00.000Z",
        },
        scheduleNeedsRefresh: false,
        surveyStatus: "queued",
        surveySubmissionIds: [submissionId],
      });
    },
    {
      ownerId: PATIENT_A,
      definition: DEFINITION_V1,
      scaleId: SCALE_ID,
      submissionId: SUBMISSION_A,
      dailyDraftId: DAILY_DRAFT_ID,
      dailyEntryId: DAILY_ENTRY_ID,
      operationId: DAILY_OPERATION_ID,
    },
  );
}

function dailyVoicePayload(entryId: string, operationId: string) {
  return {
    entry_id: entryId,
    operation_id: operationId,
    kind: "daily" as const,
    occurred_at: "2026-10-23T10:00:00.000Z",
    daily_on: "2026-10-23",
    mood_level: 3,
    mood_words: [],
    duration_ms: 12_000,
    other_medicine_text: null,
  };
}

test("finalizes daily voice, audio, and assessment drafts atomically", async ({ page }) => {
  await prepareVoiceDraft(page);
  const result = await page.evaluate(
    async ({ ownerId, dailyDraftId, dailyEntryId, assessmentPayload, voicePayload }) => {
      const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
      const { DiaryStore } = await import("/patient/src/diary/store.ts");
      const assessments = await AssessmentStore.open();
      const diary = await DiaryStore.open();
      const voiceBlob = new Blob(["synthetic audio"], { type: "audio/webm" });
      const saved = await diary.finalizeDailyCheckIn({
        ownerId,
        draftId: dailyDraftId,
        expectedDraftRevision: 1,
        surveyStatus: "queued",
        surveySubmissionIds: [assessmentPayload.submission_id],
        assessmentDrafts: [
          {
            ownerId,
            scaleId: assessmentPayload.scale_id,
            expectedRevision: 1,
            payload: assessmentPayload,
          },
        ],
        payload: voicePayload,
        voiceBlob,
      });
      const audio = await diary.getAudioBlob(ownerId, dailyEntryId);
      return {
        saved,
        entries: await diary.listEntries(ownerId),
        diaryOutbox: await diary.getOutboxForOwner(ownerId),
        assessmentOutbox: await assessments.getOutboxForOwner(ownerId),
        dailyDrafts: await diary.listDrafts(ownerId),
        audio: audio ? { type: audio.type, text: await audio.text() } : null,
      };
    },
    {
      ownerId: PATIENT_A,
      dailyDraftId: DAILY_DRAFT_ID,
      dailyEntryId: DAILY_ENTRY_ID,
      assessmentPayload: payload(),
      voicePayload: dailyVoicePayload(DAILY_ENTRY_ID, DAILY_OPERATION_ID),
    },
  );

  expect(result.saved).toMatchObject({ status: "queued" });
  expect(result.entries).toHaveLength(1);
  expect(result.entries[0]).toMatchObject({
    entry_id: DAILY_ENTRY_ID,
    kind: "daily",
    text: null,
    sync_state: "pending",
    survey_status: "queued",
  });
  expect(result.diaryOutbox).toHaveLength(1);
  expect(result.diaryOutbox[0]).toMatchObject({
    action: "voice",
    entry_id: DAILY_ENTRY_ID,
    payload: { kind: "daily", duration_ms: 12_000 },
  });
  expect(result.audio).toEqual({ type: "audio/webm", text: "synthetic audio" });
  expect(result.assessmentOutbox).toHaveLength(1);
  expect(result.dailyDrafts).toEqual([]);
});

test("rolls back voice blob, entry, assessment, and draft together on an IndexedDB abort", async ({
  page,
}) => {
  await prepareVoiceDraft(page);
  const result = await page.evaluate(
    async ({ ownerId, dailyDraftId, dailyEntryId, assessmentPayload, voicePayload }) => {
      const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
      const { DiaryStore } = await import("/patient/src/diary/store.ts");
      const assessments = await AssessmentStore.open();
      const diary = await DiaryStore.open();
      const objectStore = IDBObjectStore.prototype;
      const nativeDelete = objectStore.delete;
      objectStore.delete = function (...args) {
        const request = Reflect.apply(nativeDelete, this, args) as IDBRequest;
        if (this.name === "diaryDrafts") {
          request.addEventListener("success", () => request.transaction?.abort(), { once: true });
        }
        return request;
      };
      let failure = "";
      try {
        await diary.finalizeDailyCheckIn({
          ownerId,
          draftId: dailyDraftId,
          expectedDraftRevision: 1,
          surveyStatus: "queued",
          surveySubmissionIds: [assessmentPayload.submission_id],
          assessmentDrafts: [
            {
              ownerId,
              scaleId: assessmentPayload.scale_id,
              expectedRevision: 1,
              payload: assessmentPayload,
            },
          ],
          payload: voicePayload,
          voiceBlob: new Blob(["synthetic audio"], { type: "audio/webm" }),
        });
      } catch (error) {
        failure = error instanceof Error ? error.message : String(error);
      } finally {
        objectStore.delete = nativeDelete;
      }
      const audio = await diary.getAudioBlob(ownerId, dailyEntryId);
      return {
        failure,
        entries: await diary.listEntries(ownerId),
        diaryOutbox: await diary.getOutboxForOwner(ownerId),
        assessmentOutbox: await assessments.getOutboxForOwner(ownerId),
        diaryDrafts: await diary.listDrafts(ownerId),
        assessmentDraft: await assessments.getDraft({
          ownerId,
          scaleId: assessmentPayload.scale_id,
        }),
        audio: audio ? { type: audio.type, text: await audio.text() } : null,
      };
    },
    {
      ownerId: PATIENT_A,
      dailyDraftId: DAILY_DRAFT_ID,
      dailyEntryId: DAILY_ENTRY_ID,
      assessmentPayload: payload(),
      voicePayload: dailyVoicePayload(DAILY_ENTRY_ID, DAILY_OPERATION_ID),
    },
  );

  expect(result.failure).toMatch(/abort|finished|inactive|InvalidStateError/i);
  expect(result.entries).toEqual([]);
  expect(result.diaryOutbox).toEqual([]);
  expect(result.assessmentOutbox).toEqual([]);
  expect(result.audio).toBeNull();
  expect(result.diaryDrafts).toHaveLength(1);
  expect(result.assessmentDraft).not.toBeNull();
});
