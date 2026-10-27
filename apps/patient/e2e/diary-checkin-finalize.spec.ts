import type { Schemas } from "@compass/api-client";
import { expect, test } from "@playwright/test";
import { DEFINITION_V1, PATIENT_A, payload, SCALE_ID, SUBMISSION_A } from "./assessment-fixtures";
import { mockAssessmentAuth, openPatientPage } from "./assessment-test-helpers";

const DAILY_DRAFT_ID = "00000000-0000-4000-8000-0000000000e1";
const DAILY_ENTRY_ID = "00000000-0000-4000-8000-0000000000e2";
const DAILY_OPERATION_ID = "00000000-0000-4000-8000-0000000000e3";

test.beforeEach(async ({ context }) => mockAssessmentAuth(context));

async function prepareDrafts(page: import("@playwright/test").Page): Promise<void> {
  await openPatientPage(page);
  await page.evaluate(
    async ({
      ownerId,
      definition,
      scaleId,
      submissionId,
      dailyDraftId,
      dailyEntryId,
      dailyOperationId,
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
        operationId: dailyOperationId,
        expectedRevision: 0,
        mode: "daily",
        entryKind: "daily",
        dailyOn: "2026-10-23",
        occurredAt: "2026-10-23T10:00:00.000Z",
        step: "survey",
        moodLevel: 3,
        moodWords: [],
        text: "Ежедневный рассказ",
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
      dailyOperationId: DAILY_OPERATION_ID,
    },
  );
}

test("commits a daily entry with its not-yet-queued assessment in one local transaction", async ({
  page,
}) => {
  await prepareDrafts(page);
  const result = await page.evaluate(
    async ({ ownerId, dailyDraftId, dailyEntryId, dailyOperationId, assessmentPayload }) => {
      const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
      const { DiaryStore } = await import("/patient/src/diary/store.ts");
      const assessments = await AssessmentStore.open();
      const diary = await DiaryStore.open();
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
        payload: {
          entry_id: dailyEntryId,
          operation_id: dailyOperationId,
          kind: "daily",
          occurred_at: "2026-10-23T10:00:00.000Z",
          daily_on: "2026-10-23",
          mood_level: 3,
          mood_words: [],
          text: "Ежедневный рассказ",
        },
      });
      return {
        saved,
        entries: await diary.listEntries(ownerId),
        diaryOutbox: await diary.getOutboxForOwner(ownerId),
        assessmentOutbox: await assessments.getOutboxForOwner(ownerId),
        dailyDrafts: await diary.listDrafts(ownerId),
      };
    },
    {
      ownerId: PATIENT_A,
      dailyDraftId: DAILY_DRAFT_ID,
      dailyEntryId: DAILY_ENTRY_ID,
      dailyOperationId: DAILY_OPERATION_ID,
      assessmentPayload: payload(),
    },
  );

  expect(result.saved).toMatchObject({ status: "queued" });
  expect(result.entries).toHaveLength(1);
  expect(result.entries[0]).toMatchObject({
    entry_id: DAILY_ENTRY_ID,
    sync_state: "pending",
    survey_status: "queued",
    survey_submission_ids: [SUBMISSION_A],
  });
  expect(result.diaryOutbox).toHaveLength(1);
  expect(result.assessmentOutbox).toHaveLength(1);
  expect(result.assessmentOutbox[0]).toMatchObject({ submission_id: SUBMISSION_A });
  expect(result.dailyDrafts).toEqual([]);
});

test("rolls back diary and survey writes together on an IndexedDB abort", async ({ page }) => {
  await prepareDrafts(page);
  const result = await page.evaluate(
    async ({ ownerId, dailyDraftId, dailyEntryId, dailyOperationId, assessmentPayload }) => {
      const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
      const { DiaryStore } = await import("/patient/src/diary/store.ts");
      const assessments = await AssessmentStore.open();
      const diary = await DiaryStore.open();
      const objectStore = IDBObjectStore.prototype;
      const nativeAdd = objectStore.add;
      objectStore.add = function (...args) {
        const request = Reflect.apply(nativeAdd, this, args) as IDBRequest;
        if (this.name === "outbox") {
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
          payload: {
            entry_id: dailyEntryId,
            operation_id: dailyOperationId,
            kind: "daily",
            occurred_at: "2026-10-23T10:00:00.000Z",
            daily_on: "2026-10-23",
            mood_level: 3,
            mood_words: [],
            text: "Ежедневный рассказ",
          },
        });
      } catch (error) {
        failure = error instanceof Error ? error.message : String(error);
      } finally {
        objectStore.add = nativeAdd;
      }
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
      };
    },
    {
      ownerId: PATIENT_A,
      dailyDraftId: DAILY_DRAFT_ID,
      dailyEntryId: DAILY_ENTRY_ID,
      dailyOperationId: DAILY_OPERATION_ID,
      assessmentPayload: payload(),
    },
  );

  expect(result.failure).toMatch(/abort/i);
  expect(result.entries).toEqual([]);
  expect(result.diaryOutbox).toEqual([]);
  expect(result.assessmentOutbox).toEqual([]);
  expect(result.diaryDrafts).toHaveLength(1);
  expect(result.assessmentDraft).not.toBeNull();
});

test("resumes a pending survey without creating a second daily entry", async ({ page }) => {
  await prepareDrafts(page);
  const result = await page.evaluate(
    async ({ ownerId, dailyDraftId, dailyEntryPayload, assessmentPayload }) => {
      const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
      const { DiaryStore } = await import("/patient/src/diary/store.ts");
      const assessments = await AssessmentStore.open();
      const diary = await DiaryStore.open();
      const first = await diary.finalizeDailyCheckIn({
        ownerId,
        draftId: dailyDraftId,
        expectedDraftRevision: 1,
        surveyStatus: "pending",
        surveySubmissionIds: [],
        assessmentDrafts: [],
        payload: dailyEntryPayload,
      });
      const continuation = await diary.getDraft(ownerId, dailyDraftId);
      const second = await diary.finalizeDailyCheckIn({
        ownerId,
        draftId: dailyDraftId,
        expectedDraftRevision: continuation?.revision ?? 0,
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
        payload: dailyEntryPayload,
      });
      return {
        first,
        continuation,
        second,
        entries: await diary.listEntries(ownerId),
        diaryOutbox: await diary.getOutboxForOwner(ownerId),
        assessmentOutbox: await assessments.getOutboxForOwner(ownerId),
        drafts: await diary.listDrafts(ownerId),
      };
    },
    {
      ownerId: PATIENT_A,
      dailyDraftId: DAILY_DRAFT_ID,
      dailyEntryId: DAILY_ENTRY_ID,
      dailyOperationId: DAILY_OPERATION_ID,
      dailyEntryPayload: dailyPayload(DAILY_ENTRY_ID, DAILY_OPERATION_ID),
      assessmentPayload: payload(),
    },
  );

  expect(result.first).toMatchObject({ status: "queued" });
  expect(result.continuation).toMatchObject({ revision: 2, survey_status: "pending" });
  expect(result.second).toMatchObject({ status: "replayed" });
  expect(result.entries).toHaveLength(1);
  expect(result.entries[0]).toMatchObject({
    survey_status: "queued",
    survey_submission_ids: [SUBMISSION_A],
  });
  expect(result.diaryOutbox).toHaveLength(1);
  expect(result.assessmentOutbox).toHaveLength(1);
  expect(result.drafts).toEqual([]);
});

test("keeps an already queued assessment receipt without creating another submission", async ({
  page,
}) => {
  await prepareDrafts(page);
  const result = await page.evaluate(
    async ({ ownerId, dailyDraftId, dailyEntryPayload, assessmentPayload }) => {
      const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
      const { DiaryStore } = await import("/patient/src/diary/store.ts");
      const assessments = await AssessmentStore.open();
      const diary = await DiaryStore.open();
      await assessments.enqueueDraft({
        ownerId,
        scaleId: assessmentPayload.scale_id,
        expectedRevision: 1,
        payload: assessmentPayload,
      });
      const saved = await diary.finalizeDailyCheckIn({
        ownerId,
        draftId: dailyDraftId,
        expectedDraftRevision: 1,
        surveyStatus: "queued",
        surveySubmissionIds: [assessmentPayload.submission_id],
        assessmentDrafts: [],
        payload: dailyEntryPayload,
      });
      return {
        saved,
        entries: await diary.listEntries(ownerId),
        assessmentOutbox: await assessments.getOutboxForOwner(ownerId),
      };
    },
    {
      ownerId: PATIENT_A,
      dailyDraftId: DAILY_DRAFT_ID,
      dailyEntryId: DAILY_ENTRY_ID,
      dailyOperationId: DAILY_OPERATION_ID,
      dailyEntryPayload: dailyPayload(DAILY_ENTRY_ID, DAILY_OPERATION_ID),
      assessmentPayload: payload(),
    },
  );

  expect(result.saved).toMatchObject({ status: "queued" });
  expect(result.entries).toHaveLength(1);
  expect(result.assessmentOutbox).toHaveLength(1);
  expect(result.assessmentOutbox[0]).toMatchObject({ submission_id: SUBMISSION_A });
});

function dailyPayload(entryId: string, operationId: string): Schemas["DiaryEntryCreateIn"] {
  return {
    entry_id: entryId,
    operation_id: operationId,
    kind: "daily" as const,
    occurred_at: "2026-10-23T10:00:00.000Z",
    daily_on: "2026-10-23",
    mood_level: 3,
    mood_words: [],
    text: "Ежедневный рассказ",
  };
}
