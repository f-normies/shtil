import { expect, test } from "@playwright/test";
import { mockAssessmentAuth, openPatientPage } from "./assessment-test-helpers";

const OWNER_ID = "00000000-0000-4000-8000-0000000000a1";

test.beforeEach(async ({ context }) => mockAssessmentAuth(context));

async function rememberOwner(page: import("@playwright/test").Page): Promise<void> {
  await page.evaluate(async (ownerId) => {
    const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
    await (await AssessmentStore.open()).rememberOnlinePatient({ id: ownerId, role: "patient" });
  }, OWNER_ID);
}

test("persists create, edit, and hide commands in order and sends each immutable body", async ({
  page,
}) => {
  await openPatientPage(page);
  await rememberOwner(page);

  const result = await page.evaluate(async (ownerId) => {
    const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    const { createDiarySyncEngine } = await import("/patient/src/diary/sync.ts");
    const assessments = await AssessmentStore.open();
    const diary = await DiaryStore.open();
    const entryId = "00000000-0000-4000-8000-0000000000b1";
    const createId = "00000000-0000-4000-8000-0000000000c1";
    const updateId = "00000000-0000-4000-8000-0000000000c2";
    const hideId = "00000000-0000-4000-8000-0000000000c3";
    await diary.enqueueEntry({
      ownerId,
      payload: {
        entry_id: entryId,
        operation_id: createId,
        kind: "text",
        occurred_at: "2026-10-23T10:00:00.000Z",
        text: "Первая версия",
      },
    });
    await diary.enqueueUpdate({
      ownerId,
      entryId,
      payload: { operation_id: updateId, expected_revision: 1, text: "Исправленная версия" },
    });
    await diary.enqueueHide({
      ownerId,
      entryId,
      payload: { operation_id: hideId, expected_revision: 2 },
    });

    const queued = await diary.getOutboxForOwner(ownerId);
    const calls: string[] = [];
    const engine = createDiarySyncEngine(diary, assessments, {
      getCurrentUser: async () => ({ id: ownerId, role: "patient" }),
      getSessionGeneration: () => 7,
      createEntry: async (payload) => {
        calls.push(`create:${payload.operation_id}:${payload.text}`);
        return {
          status: 201,
          receipt: {
            entry_id: entryId,
            operation_id: payload.operation_id,
            hidden: false,
            received_at: "2026-10-23T10:01:00.000Z",
            revision: 1,
          },
        };
      },
      updateEntry: async (id, payload) => {
        calls.push(`update:${id}:${payload.operation_id}:${payload.text}`);
        return {
          status: 200,
          receipt: {
            entry_id: id,
            operation_id: payload.operation_id,
            hidden: false,
            received_at: "2026-10-23T10:02:00.000Z",
            revision: 2,
          },
        };
      },
      hideEntry: async (id, payload) => {
        calls.push(`hide:${id}:${payload.operation_id}`);
        return {
          status: 200,
          receipt: {
            entry_id: id,
            operation_id: payload.operation_id,
            hidden: true,
            received_at: "2026-10-23T10:03:00.000Z",
            revision: 2,
          },
        };
      },
    });
    const outcome = await engine.sync(ownerId, 7);
    return {
      queued: queued.map((command) => ({
        action: command.action,
        operationId: command.operation_id,
      })),
      calls,
      outcome,
      remaining: await diary.getOutboxForOwner(ownerId),
      visibleEntries: await diary.listEntries(ownerId),
    };
  }, OWNER_ID);

  expect(result.queued).toEqual([
    { action: "create", operationId: "00000000-0000-4000-8000-0000000000c1" },
    { action: "update", operationId: "00000000-0000-4000-8000-0000000000c2" },
    { action: "hide", operationId: "00000000-0000-4000-8000-0000000000c3" },
  ]);
  expect(result.calls).toEqual([
    "create:00000000-0000-4000-8000-0000000000c1:Первая версия",
    "update:00000000-0000-4000-8000-0000000000b1:00000000-0000-4000-8000-0000000000c2:Исправленная версия",
    "hide:00000000-0000-4000-8000-0000000000b1:00000000-0000-4000-8000-0000000000c3",
  ]);
  expect(result.outcome).toMatchObject({ status: "complete", submitted: 3 });
  expect(result.remaining).toEqual([]);
  expect(result.visibleEntries).toEqual([]);
});

test("a failed command blocks only later operations for that entry", async ({ page }) => {
  await openPatientPage(page);
  await rememberOwner(page);

  const result = await page.evaluate(async (ownerId) => {
    const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    const { createDiarySyncEngine } = await import("/patient/src/diary/sync.ts");
    const assessments = await AssessmentStore.open();
    const diary = await DiaryStore.open();
    const blockedEntryId = "00000000-0000-4000-8000-0000000000b2";
    await diary.enqueueEntry({
      ownerId,
      payload: {
        entry_id: blockedEntryId,
        operation_id: "00000000-0000-4000-8000-0000000000c4",
        kind: "text",
        occurred_at: "2026-10-23T10:00:00.000Z",
        text: "Ждёт создания",
      },
    });
    await diary.enqueueUpdate({
      ownerId,
      entryId: blockedEntryId,
      payload: {
        operation_id: "00000000-0000-4000-8000-0000000000c5",
        expected_revision: 1,
        text: "Ждёт обновления",
      },
    });
    await diary.enqueueHide({
      ownerId,
      entryId: blockedEntryId,
      payload: { operation_id: "00000000-0000-4000-8000-0000000000c6", expected_revision: 2 },
    });
    const laterEntryId = "00000000-0000-4000-8000-0000000000b3";
    await diary.enqueueEntry({
      ownerId,
      payload: {
        entry_id: laterEntryId,
        operation_id: "00000000-0000-4000-8000-0000000000c7",
        kind: "text",
        occurred_at: "2026-10-23T10:01:00.000Z",
        text: "Другая запись",
      },
    });

    const calls: string[] = [];
    const engine = createDiarySyncEngine(diary, assessments, {
      getCurrentUser: async () => ({ id: ownerId, role: "patient" }),
      getSessionGeneration: () => 7,
      createEntry: async (payload) => {
        calls.push(`create:${payload.entry_id}`);
        return payload.entry_id === blockedEntryId
          ? { status: 503 }
          : {
              status: 201,
              receipt: {
                entry_id: payload.entry_id,
                operation_id: payload.operation_id,
                hidden: false,
                received_at: "2026-10-23T10:02:00.000Z",
                revision: 1,
              },
            };
      },
      updateEntry: async (id, payload) => {
        calls.push(`update:${id}:${payload.operation_id}`);
        return { status: 503 };
      },
      hideEntry: async (id, payload) => {
        calls.push(`hide:${id}:${payload.operation_id}`);
        return { status: 503 };
      },
    });
    const outcome = await engine.sync(ownerId, 7);
    return {
      calls,
      outcome,
      commands: (await diary.getOutboxForOwner(ownerId)).map((command) => ({
        entryId: command.entry_id,
        action: command.action,
        state: command.state,
      })),
      entries: await diary.listEntries(ownerId),
    };
  }, OWNER_ID);

  expect(result.calls).toEqual([
    "create:00000000-0000-4000-8000-0000000000b2",
    "create:00000000-0000-4000-8000-0000000000b3",
  ]);
  expect(result.outcome).toMatchObject({ status: "waiting", submitted: 1 });
  expect(result.commands).toEqual([
    { entryId: "00000000-0000-4000-8000-0000000000b2", action: "create", state: "pending" },
    { entryId: "00000000-0000-4000-8000-0000000000b2", action: "update", state: "pending" },
    { entryId: "00000000-0000-4000-8000-0000000000b2", action: "hide", state: "pending" },
  ]);
  expect(result.entries).toContainEqual(
    expect.objectContaining({
      entry_id: "00000000-0000-4000-8000-0000000000b3",
      sync_state: "synced",
    }),
  );
});

test("a late create receipt leaves a newer local edit and its command intact", async ({ page }) => {
  await openPatientPage(page);
  await rememberOwner(page);

  const result = await page.evaluate(async (ownerId) => {
    const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    const { createDiarySyncEngine } = await import("/patient/src/diary/sync.ts");
    const assessments = await AssessmentStore.open();
    const diary = await DiaryStore.open();
    const entryId = "00000000-0000-4000-8000-0000000000b4";
    const createId = "00000000-0000-4000-8000-0000000000c8";
    const updateId = "00000000-0000-4000-8000-0000000000c9";
    await diary.enqueueEntry({
      ownerId,
      payload: {
        entry_id: entryId,
        operation_id: createId,
        kind: "text",
        occurred_at: "2026-10-23T10:00:00.000Z",
        text: "Первый черновик",
      },
    });
    let markStarted: () => void = () => {};
    let resolveCreate: (response: unknown) => void = () => {};
    const started = new Promise<void>((resolve) => {
      markStarted = resolve;
    });
    const delayed = new Promise<unknown>((resolve) => {
      resolveCreate = resolve;
    });
    const engine = createDiarySyncEngine(diary, assessments, {
      getCurrentUser: async () => ({ id: ownerId, role: "patient" }),
      getSessionGeneration: () => 7,
      createEntry: async () => {
        markStarted();
        return delayed as never;
      },
    });
    const syncing = engine.sync(ownerId, 7);
    await started;
    await diary.enqueueUpdate({
      ownerId,
      entryId,
      payload: { operation_id: updateId, expected_revision: 1, text: "Новая локальная редакция" },
    });
    resolveCreate({
      status: 201,
      receipt: {
        entry_id: entryId,
        operation_id: createId,
        hidden: false,
        received_at: "2026-10-23T10:01:00.000Z",
        revision: 1,
      },
    });
    const outcome = await syncing;
    return {
      outcome,
      entry: (await diary.listEntries(ownerId))[0],
      commands: await diary.getOutboxForOwner(ownerId),
    };
  }, OWNER_ID);

  expect(result.outcome).toMatchObject({ status: "complete", submitted: 1 });
  expect(result.entry).toMatchObject({
    entry_id: "00000000-0000-4000-8000-0000000000b4",
    text: "Новая локальная редакция",
    revision: 2,
    sync_state: "pending",
  });
  expect(result.commands).toHaveLength(1);
  expect(result.commands[0]).toMatchObject({
    action: "update",
    operation_id: "00000000-0000-4000-8000-0000000000c9",
  });
});

test("retries an owner-bound voice Blob with the same upload operation ID after reload", async ({
  page,
}) => {
  await openPatientPage(page);
  await rememberOwner(page);
  await page.evaluate(async (ownerId) => {
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    const diary = await DiaryStore.open();
    await diary.enqueueVoiceEntry({
      ownerId,
      payload: {
        operation_id: "00000000-0000-4000-8000-0000000000ca",
        entry_id: "00000000-0000-4000-8000-0000000000b5",
        kind: "voice",
        daily_on: null,
        mood_level: null,
        other_medicine_text: null,
        occurred_at: "2026-10-23T10:00:00.000Z",
        duration_ms: 1200,
        mood_words: [],
      },
      blob: new Blob(["stopped voice recording"], { type: "audio/webm" }),
    });
  }, OWNER_ID);

  await page.reload();
  const result = await page.evaluate(async (ownerId) => {
    const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    const { createDiarySyncEngine } = await import("/patient/src/diary/sync.ts");
    const assessments = await AssessmentStore.open();
    const diary = await DiaryStore.open();
    const attempts: Array<{ operationId: string; bytes: string }> = [];
    const engine = createDiarySyncEngine(diary, assessments, {
      getCurrentUser: async () => ({ id: ownerId, role: "patient" }),
      getSessionGeneration: () => 7,
      createEntry: async () => ({ status: null }),
      uploadVoice: async (payload, blob) => {
        attempts.push({ operationId: payload.operation_id, bytes: await blob.text() });
        if (attempts.length === 1) return { status: 503 };
        return {
          status: 201,
          receipt: {
            entry_id: payload.entry_id,
            operation_id: payload.operation_id,
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
    return { first, retained, second, attempts, remaining: await diary.getOutboxForOwner(ownerId) };
  }, OWNER_ID);

  expect(result.first).toMatchObject({ status: "waiting" });
  expect(result.retained).toHaveLength(1);
  expect(result.retained[0]).toMatchObject({
    action: "voice",
    operation_id: "00000000-0000-4000-8000-0000000000ca",
  });
  expect(result.second).toMatchObject({ status: "complete", submitted: 1 });
  expect(result.attempts).toEqual([
    { operationId: "00000000-0000-4000-8000-0000000000ca", bytes: "stopped voice recording" },
    { operationId: "00000000-0000-4000-8000-0000000000ca", bytes: "stopped voice recording" },
  ]);
  expect(result.remaining).toEqual([]);
});
