import { expect, test } from "@playwright/test";
import { mockAssessmentAuth, openPatientPage } from "./assessment-test-helpers";

const OWNER_A = "00000000-0000-4000-8000-0000000000a1";
const OWNER_B = "00000000-0000-4000-8000-0000000000a2";

test.beforeEach(async ({ context }) => mockAssessmentAuth(context));

async function queueEntry(
  page: import("@playwright/test").Page,
  entryId: string,
  operationId: string,
  text: string,
): Promise<void> {
  await page.evaluate(
    async ({ ownerId, newEntryId, newOperationId, entryText }) => {
      const { DiaryStore } = await import("/patient/src/diary/store.ts");
      await (await DiaryStore.open()).enqueueEntry({
        ownerId,
        payload: {
          entry_id: newEntryId,
          operation_id: newOperationId,
          kind: "text",
          occurred_at: "2026-10-23T10:00:00.000Z",
          text: entryText,
        },
      });
    },
    { ownerId: OWNER_A, newEntryId: entryId, newOperationId: operationId, entryText: text },
  );
}

test("retries a transient diary failure with the same command and confirms only after success", async ({
  page,
}) => {
  await openPatientPage(page);
  await page.evaluate(async (ownerId) => {
    const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
    await (await AssessmentStore.open()).rememberOnlinePatient({ id: ownerId, role: "patient" });
  }, OWNER_A);
  const entryId = "00000000-0000-4000-8000-0000000000b1";
  const operationId = "00000000-0000-4000-8000-0000000000c1";
  await queueEntry(page, entryId, operationId, "Повторяемая команда");

  const result = await page.evaluate(
    async ({ ownerId, newEntryId, newOperationId }) => {
      const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
      const { DiaryStore } = await import("/patient/src/diary/store.ts");
      const { createDiarySyncEngine } = await import("/patient/src/diary/sync.ts");
      const assessments = await AssessmentStore.open();
      const diary = await DiaryStore.open();
      const submittedIds: string[] = [];
      let attempts = 0;
      const engine = createDiarySyncEngine(diary, assessments, {
        getCurrentUser: async () => ({ id: ownerId, role: "patient" as const }),
        getSessionGeneration: () => 7,
        createEntry: async (payload) => {
          attempts += 1;
          submittedIds.push(payload.operation_id);
          return attempts === 1
            ? { status: 503 }
            : {
                status: 201,
                receipt: {
                  entry_id: newEntryId,
                  operation_id: newOperationId,
                  hidden: false,
                  received_at: "2026-10-23T10:01:00.000Z",
                  revision: 1,
                },
              };
        },
      });
      const first = await engine.sync(ownerId, 7);
      const retained = await diary.getOutboxForOwner(ownerId);
      const second = await engine.sync(ownerId, 7);
      return {
        first,
        retained,
        second,
        attempts,
        submittedIds,
        outbox: await diary.getOutboxForOwner(ownerId),
        entries: await diary.listEntries(ownerId),
      };
    },
    { ownerId: OWNER_A, newEntryId: entryId, newOperationId: operationId },
  );

  expect(result.first).toMatchObject({ status: "waiting" });
  expect(result.retained).toHaveLength(1);
  expect(result.second).toMatchObject({ status: "complete", submitted: 1 });
  expect(result.attempts).toBe(2);
  expect(result.submittedIds).toEqual([operationId, operationId]);
  expect(result.outbox).toEqual([]);
  expect(result.entries[0]).toMatchObject({ entry_id: entryId, sync_state: "synced" });
});

test("locks diary access after a 401 and retains the unsent command", async ({ page }) => {
  await openPatientPage(page);
  await page.evaluate(async (ownerId) => {
    const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
    await (await AssessmentStore.open()).rememberOnlinePatient({ id: ownerId, role: "patient" });
  }, OWNER_A);
  await queueEntry(
    page,
    "00000000-0000-4000-8000-0000000000b2",
    "00000000-0000-4000-8000-0000000000c2",
    "Ожидает входа",
  );

  const result = await page.evaluate(async (ownerId) => {
    const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    const { openAssessmentDatabase } = await import("/patient/src/assessments/database.ts");
    const { createDiarySyncEngine } = await import("/patient/src/diary/sync.ts");
    const assessments = await AssessmentStore.open();
    const diary = await DiaryStore.open();
    const engine = createDiarySyncEngine(diary, assessments, {
      getCurrentUser: async () => ({ id: ownerId, role: "patient" as const }),
      getSessionGeneration: () => 7,
      createEntry: async () => ({ status: 401 }),
    });
    const outcome = await engine.sync(ownerId, 7);
    const pointer = await assessments.getOwnerPointer();
    const database = await openAssessmentDatabase();
    const transaction = database.transaction("diaryOutbox", "readonly");
    const retained = await new Promise<unknown[]>((resolve, reject) => {
      const request = transaction.objectStore("diaryOutbox").getAll();
      request.addEventListener("success", () => resolve(request.result), { once: true });
      request.addEventListener("error", () => reject(request.error), { once: true });
    });
    return { outcome, pointer, retained };
  }, OWNER_A);

  expect(result.outcome).toMatchObject({ status: "authentication-required" });
  expect(result.pointer).toMatchObject({ id: OWNER_A, locked: true });
  expect(result.retained).toHaveLength(1);
});

test("does not apply a late response after the authenticated owner changes", async ({ page }) => {
  await openPatientPage(page);
  await page.evaluate(async (ownerId) => {
    const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
    await (await AssessmentStore.open()).rememberOnlinePatient({ id: ownerId, role: "patient" });
  }, OWNER_A);
  const entryId = "00000000-0000-4000-8000-0000000000b3";
  const operationId = "00000000-0000-4000-8000-0000000000c3";
  await queueEntry(page, entryId, operationId, "Старый владелец");

  const result = await page.evaluate(
    async ({ ownerA, ownerB, queuedEntryId, queuedOperationId }) => {
      const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
      const { DiaryStore } = await import("/patient/src/diary/store.ts");
      const { openAssessmentDatabase } = await import("/patient/src/assessments/database.ts");
      const { createDiarySyncEngine } = await import("/patient/src/diary/sync.ts");
      const assessments = await AssessmentStore.open();
      const diary = await DiaryStore.open();
      let started: () => void = () => {};
      let finish: (result: {
        status: number;
        receipt: {
          entry_id: string;
          operation_id: string;
          hidden: boolean;
          received_at: string;
          revision: number;
        };
      }) => void = () => {};
      const requestStarted = new Promise<void>((resolve) => {
        started = resolve;
      });
      const delayedResponse = new Promise<{
        status: number;
        receipt: {
          entry_id: string;
          operation_id: string;
          hidden: boolean;
          received_at: string;
          revision: number;
        };
      }>((resolve) => {
        finish = resolve;
      });
      const engine = createDiarySyncEngine(diary, assessments, {
        getCurrentUser: async () => ({ id: ownerA, role: "patient" as const }),
        getSessionGeneration: () => 7,
        createEntry: async () => {
          started();
          return delayedResponse;
        },
      });
      const pending = engine.sync(ownerA, 7);
      await requestStarted;
      await assessments.rememberOnlinePatient({ id: ownerB, role: "patient" });
      finish({
        status: 201,
        receipt: {
          entry_id: queuedEntryId,
          operation_id: queuedOperationId,
          hidden: false,
          received_at: "2026-10-23T10:01:00.000Z",
          revision: 1,
        },
      });
      const outcome = await pending;
      const database = await openAssessmentDatabase();
      const transaction = database.transaction(["diaryOutbox", "diaryEntries"], "readonly");
      const [commands, entries] = await Promise.all([
        new Promise<unknown[]>((resolve, reject) => {
          const request = transaction.objectStore("diaryOutbox").getAll();
          request.addEventListener("success", () => resolve(request.result), { once: true });
          request.addEventListener("error", () => reject(request.error), { once: true });
        }),
        new Promise<unknown[]>((resolve, reject) => {
          const request = transaction.objectStore("diaryEntries").getAll();
          request.addEventListener("success", () => resolve(request.result), { once: true });
          request.addEventListener("error", () => reject(request.error), { once: true });
        }),
      ]);
      return { outcome, pointer: await assessments.getOwnerPointer(), commands, entries };
    },
    { ownerA: OWNER_A, ownerB: OWNER_B, queuedEntryId: entryId, queuedOperationId: operationId },
  );

  expect(result.outcome).toMatchObject({ status: "owner-mismatch" });
  expect(result.pointer).toMatchObject({ id: OWNER_B, locked: false });
  expect(result.commands).toHaveLength(1);
  expect(result.entries[0]).toMatchObject({ entry_id: entryId, sync_state: "pending" });
});

test("keeps a local conflict and its fetched remote snapshot side by side", async ({ page }) => {
  await openPatientPage(page);
  await page.evaluate(async (ownerId) => {
    const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
    await (await AssessmentStore.open()).rememberOnlinePatient({ id: ownerId, role: "patient" });
  }, OWNER_A);
  await queueEntry(
    page,
    "00000000-0000-4000-8000-0000000000b4",
    "00000000-0000-4000-8000-0000000000c4",
    "Локальная версия",
  );

  const result = await page.evaluate(async (ownerId) => {
    const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    const { createDiarySyncEngine } = await import("/patient/src/diary/sync.ts");
    const assessments = await AssessmentStore.open();
    const diary = await DiaryStore.open();
    const remote = {
      audio: null,
      daily_on: null,
      entry_id: "00000000-0000-4000-8000-0000000000b4",
      kind: "text" as const,
      mood_level: null,
      mood_words: [],
      occurred_at: "2026-10-23T10:00:00.000Z",
      other_medicine_text: null,
      received_at: "2026-10-23T11:00:00.000Z",
      revision: 2,
      source_statuses: [],
      text: "Серверная версия",
    };
    const engine = createDiarySyncEngine(diary, assessments, {
      getCurrentUser: async () => ({ id: ownerId, role: "patient" as const }),
      getSessionGeneration: () => 7,
      createEntry: async () => ({ status: 409, code: "revision_conflict" }),
      fetchEntry: async () => remote,
    });
    const outcome = await engine.sync(ownerId, 7);
    return {
      outcome,
      entry: (await diary.listEntries(ownerId))[0],
      command: (await diary.getOutboxForOwner(ownerId))[0],
    };
  }, OWNER_A);

  expect(result.outcome).toMatchObject({ status: "complete", blocked: 1 });
  expect(result.entry).toMatchObject({
    text: "Локальная версия",
    sync_state: "conflict",
    remote_snapshot: { text: "Серверная версия", revision: 2 },
  });
  expect(result.command).toMatchObject({
    state: "conflict",
    payload: { text: "Локальная версия" },
  });
});
