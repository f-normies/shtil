import type { Schemas } from "@compass/api-client";
import { expect, test } from "@playwright/test";
import { PATIENT_A, PATIENT_B } from "./assessment-fixtures";
import { mockAssessmentAuth, openPatientPage } from "./assessment-test-helpers";

test.beforeEach(async ({ context }) => mockAssessmentAuth(context));

test("keeps successful context, schedule, prescriptions, and latest message owner-bound", async ({
  page,
}) => {
  await openPatientPage(page);
  const cached = await page.evaluate(
    async ({ ownerA, ownerB }) => {
      const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
      const { DiaryStore } = await import("/patient/src/diary/store.ts");
      const assessments = await AssessmentStore.open();
      await assessments.rememberOnlinePatient({ id: ownerA, role: "patient" });
      const diary = await DiaryStore.open();
      const context = {
        daily_status: "missing" as const,
        month_entry_count: 2,
        observation_from: "2026-01-01",
        server_now: "2026-10-23T20:00:00.000Z",
        timezone: "Europe/Moscow",
        today: "2026-10-23",
      } satisfies Schemas["DiaryContextOut"];
      const prescription = {
        appearance: null,
        appearance_origin: "unknown",
        appearance_revision: 0,
        default_appearance: null,
        dose_mg: 10,
        ends_on: null,
        inn: "synthetic",
        instructions: [],
        prescription_id: "00000000-0000-4000-8000-000000000041",
        reactions: [],
        schedule: "once_daily",
        started_at: "2026-10-01T00:00:00.000Z",
        times_per_day: 1,
        trade_name: "Synthetic medicine",
      } satisfies Schemas["ScheduleItem"];
      const message = {
        analysis_skip_reason: "doctor_message",
        analysis_status: null,
        client_message_id: "00000000-0000-4000-8000-000000000042",
        created_at: "2026-10-23T19:00:00.000Z",
        duration_ms: null,
        id: "00000000-0000-4000-8000-000000000043",
        kind: "text",
        sender_id: "00000000-0000-4000-8000-000000000044",
        seq: 1,
        text: "Синтетическое сообщение",
        transcription_error_code: null,
        transcription_status: null,
        transcription_text: null,
      } satisfies Schemas["MessageOut"];

      await diary.cacheContext(ownerA, context);
      await diary.cacheSchedule(ownerA, { today: "2026-10-23", items: [] });
      await diary.cachePrescriptions(ownerA, [prescription]);
      await diary.cacheLatestMessage(ownerA, message);
      await diary.cacheContext(ownerA, { ...context, month_entry_count: 3 });
      const beforeSwitch = await diary.getContextCache(ownerA);
      await assessments.rememberOnlinePatient({ id: ownerB, role: "patient" });
      let oldOwnerDenied = false;
      try {
        await diary.getContextCache(ownerA);
      } catch {
        oldOwnerDenied = true;
      }
      return {
        beforeSwitch,
        oldOwnerDenied,
        newOwnerCache: await diary.getContextCache(ownerB),
      };
    },
    { ownerA: PATIENT_A, ownerB: PATIENT_B },
  );

  expect(cached.beforeSwitch).toMatchObject({
    context: { month_entry_count: 3, timezone: "Europe/Moscow" },
    schedule_snapshot: { today: "2026-10-23" },
    prescriptions_snapshot: [{ prescription_id: "00000000-0000-4000-8000-000000000041" }],
    latest_message: { id: "00000000-0000-4000-8000-000000000043" },
  });
  expect(cached.beforeSwitch?.context_fetched_at).toBeTruthy();
  expect(cached.beforeSwitch?.prescriptions_fetched_at).toBeTruthy();
  expect(cached.beforeSwitch?.latest_message_fetched_at).toBeTruthy();
  expect(cached.oldOwnerDenied).toBe(true);
  expect(cached.newOwnerCache).toBeNull();
});

test("logout counts and explicit cleanup include this owner's diary draft, queue, and audio", async ({
  page,
}) => {
  await openPatientPage(page);
  const result = await page.evaluate(async (ownerId) => {
    const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    const assessments = await AssessmentStore.open();
    await assessments.rememberOnlinePatient({ id: ownerId, role: "patient" });
    const diary = await DiaryStore.open();
    const entryId = "00000000-0000-4000-8000-000000000051";
    await diary.saveDraft({
      ownerId,
      draftId: "00000000-0000-4000-8000-000000000052",
      expectedRevision: 0,
      mode: "quick",
      entryKind: "text",
      occurredAt: "2026-10-23T10:00:00.000Z",
      step: "story",
      moodLevel: null,
      moodWords: [],
      text: "Сохранённый черновик",
    });
    await diary.enqueueEntry({
      ownerId,
      payload: {
        entry_id: entryId,
        operation_id: "00000000-0000-4000-8000-000000000053",
        kind: "text",
        occurred_at: "2026-10-23T10:00:00.000Z",
        mood_words: [],
        text: "Ожидает отправки",
      },
    });
    await diary.saveAudioBlob(
      ownerId,
      entryId,
      new Blob(["synthetic audio"], { type: "audio/webm" }),
    );
    const before = await assessments.getOwnerDataCounts(ownerId);
    await assessments.clearOwnerDataAfterConfirmation(ownerId);
    await assessments.rememberOnlinePatient({ id: ownerId, role: "patient" });
    return {
      before,
      after: await assessments.getOwnerDataCounts(ownerId),
      drafts: await diary.listDrafts(ownerId),
      entries: await diary.listEntries(ownerId),
      diaryOutbox: await diary.getOutboxForOwner(ownerId),
      audio: await diary.getAudioBlob(ownerId, entryId),
      cache: await diary.getContextCache(ownerId),
    };
  }, PATIENT_A);

  expect(result.before).toEqual({ drafts: 1, outbox: 1, audio: 1 });
  expect(result.after).toEqual({ drafts: 0, outbox: 0, audio: 0 });
  expect(result.drafts).toEqual([]);
  expect(result.entries).toEqual([]);
  expect(result.diaryOutbox).toEqual([]);
  expect(result.audio).toBeNull();
  expect(result.cache).toBeNull();
});
