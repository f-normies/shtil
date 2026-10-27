import type { Schemas } from "@compass/api-client";
import { expect, test } from "@playwright/test";
import { mockAssessmentAuth, openPatientPage } from "./assessment-test-helpers";

const OWNER_A = "00000000-0000-4000-8000-0000000000a1";
const OWNER_B = "00000000-0000-4000-8000-0000000000a2";

test.beforeEach(async ({ context }) => mockAssessmentAuth(context));

async function queueEntry(
  page: import("@playwright/test").Page,
  entryId: string,
  operationId: string,
): Promise<void> {
  await page.evaluate(
    async ({ ownerId, newEntryId, newOperationId }) => {
      const { DiaryStore } = await import("/patient/src/diary/store.ts");
      await (await DiaryStore.open()).enqueueEntry({
        ownerId,
        payload: {
          entry_id: newEntryId,
          operation_id: newOperationId,
          kind: "text",
          occurred_at: "2026-10-23T10:00:00.000Z",
          text: "Локальная версия",
        },
      });
    },
    { ownerId: OWNER_A, newEntryId: entryId, newOperationId: operationId },
  );
}

test("keeps a failed intake mark queued without blocking the daily entry", async ({ page }) => {
  await openPatientPage(page);
  await page.evaluate(async (ownerId) => {
    const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
    await (await AssessmentStore.open()).rememberOnlinePatient({ id: ownerId, role: "patient" });
  }, OWNER_A);
  const entryId = "00000000-0000-4000-8000-0000000000b5";
  const operationId = "00000000-0000-4000-8000-0000000000c5";
  await queueEntry(page, entryId, operationId);

  const result = await page.evaluate(
    async ({ ownerId, queuedEntryId, queuedOperationId }) => {
      const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
      const { DiaryStore } = await import("/patient/src/diary/store.ts");
      const { createDiarySyncEngine } = await import("/patient/src/diary/sync.ts");
      const assessments = await AssessmentStore.open();
      const diary = await DiaryStore.open();
      await diary.enqueueIntakeMark({
        ownerId,
        payload: {
          dose_ordinal: 0,
          occurred_at: "2026-10-23T10:00:00.000Z",
          operation_id: "00000000-0000-4000-8000-0000000000c6",
          prescription_id: "00000000-0000-4000-8000-0000000000d1",
          scheduled_on: "2026-10-23",
          status: "taken",
        },
      });
      let dailyAttempts = 0;
      const engine = createDiarySyncEngine(diary, assessments, {
        getCurrentUser: async () => ({ id: ownerId, role: "patient" as const }),
        getSessionGeneration: () => 7,
        createIntakeMark: async () => ({ status: 422, code: "invalid_intake_mark" }),
        createEntry: async () => {
          dailyAttempts += 1;
          return {
            status: 201,
            receipt: {
              entry_id: queuedEntryId,
              operation_id: queuedOperationId,
              hidden: false,
              received_at: "2026-10-23T10:01:00.000Z",
              revision: 1,
            },
          };
        },
      });
      const outcome = await engine.sync(ownerId, 7);
      return {
        outcome,
        dailyAttempts,
        entries: await diary.listEntries(ownerId),
        intakeOutbox: await diary.getIntakeOutboxForOwner(ownerId),
      };
    },
    { ownerId: OWNER_A, queuedEntryId: entryId, queuedOperationId: operationId },
  );

  expect(result.outcome).toMatchObject({ status: "complete", submitted: 1, blocked: 1 });
  expect(result.dailyAttempts).toBe(1);
  expect(result.entries[0]).toMatchObject({ entry_id: entryId, sync_state: "synced" });
  expect(result.intakeOutbox).toHaveLength(1);
  expect(result.intakeOutbox[0]).toMatchObject({ state: "blocked" });
});

test("does not apply a fetched conflict snapshot after the owner changes", async ({ page }) => {
  await openPatientPage(page);
  await page.evaluate(async (ownerId) => {
    const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
    await (await AssessmentStore.open()).rememberOnlinePatient({ id: ownerId, role: "patient" });
  }, OWNER_A);
  const entryId = "00000000-0000-4000-8000-0000000000b6";
  const operationId = "00000000-0000-4000-8000-0000000000c7";
  await queueEntry(page, entryId, operationId);

  const result = await page.evaluate(
    async ({ ownerA, ownerB, queuedEntryId, queuedOperationId }) => {
      const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
      const { DiaryStore } = await import("/patient/src/diary/store.ts");
      const { openAssessmentDatabase } = await import("/patient/src/assessments/database.ts");
      const { createDiarySyncEngine } = await import("/patient/src/diary/sync.ts");
      const assessments = await AssessmentStore.open();
      const diary = await DiaryStore.open();
      let started: () => void = () => {};
      let finish: (entry: Schemas["DiaryEntryOut"]) => void = () => {};
      const requestStarted = new Promise<void>((resolve) => {
        started = resolve;
      });
      const delayedEntry = new Promise<Schemas["DiaryEntryOut"]>((resolve) => {
        finish = resolve;
      });
      const engine = createDiarySyncEngine(diary, assessments, {
        getCurrentUser: async () => ({ id: ownerA, role: "patient" as const }),
        getSessionGeneration: () => 7,
        createEntry: async () => ({ status: 409, code: "revision_conflict" }),
        fetchEntry: async () => {
          started();
          return delayedEntry;
        },
      });
      const pending = engine.sync(ownerA, 7);
      await requestStarted;
      await assessments.rememberOnlinePatient({ id: ownerB, role: "patient" });
      finish({
        audio: null,
        daily_on: null,
        entry_id: queuedEntryId,
        kind: "text",
        mood_level: null,
        mood_words: [],
        occurred_at: "2026-10-23T10:00:00.000Z",
        other_medicine_text: null,
        received_at: "2026-10-23T11:00:00.000Z",
        revision: 2,
        source_statuses: [],
        text: "Устаревшая удалённая версия",
      });
      const outcome = await pending;
      const database = await openAssessmentDatabase();
      const transaction = database.transaction(["diaryOutbox", "diaryEntries"], "readonly");
      const [command, entry] = await Promise.all([
        new Promise<Record<string, unknown> | undefined>((resolve, reject) => {
          const request = transaction.objectStore("diaryOutbox").get([ownerA, queuedOperationId]);
          request.addEventListener("success", () => resolve(request.result), { once: true });
          request.addEventListener("error", () => reject(request.error), { once: true });
        }),
        new Promise<Record<string, unknown> | undefined>((resolve, reject) => {
          const request = transaction.objectStore("diaryEntries").get([ownerA, queuedEntryId]);
          request.addEventListener("success", () => resolve(request.result), { once: true });
          request.addEventListener("error", () => reject(request.error), { once: true });
        }),
      ]);
      return { outcome, command, entry };
    },
    { ownerA: OWNER_A, ownerB: OWNER_B, queuedEntryId: entryId, queuedOperationId: operationId },
  );

  expect(result.outcome).toMatchObject({ status: "owner-mismatch" });
  expect(result.command).toMatchObject({ state: "conflict" });
  expect(result.entry).toMatchObject({
    text: "Локальная версия",
    sync_state: "conflict",
    remote_snapshot: null,
  });
});
