import { expect, test } from "@playwright/test";
import { mockAssessmentAuth, openPatientPage } from "./assessment-test-helpers";

test.beforeEach(async ({ context }) => mockAssessmentAuth(context));

test("upgrades the assessment database and preserves its owner, draft, and queued answer", async ({
  page,
}) => {
  await openPatientPage(page);

  const result = await page.evaluate(async () => {
    const name = "clin-compass-assessments";
    const ownerId = "00000000-0000-4000-8000-0000000000a1";
    const requestValue = <T>(request: IDBRequest<T>): Promise<T> =>
      new Promise((resolve, reject) => {
        request.addEventListener("success", () => resolve(request.result), { once: true });
        request.addEventListener("error", () => reject(request.error), { once: true });
      });
    const legacyRequest = indexedDB.open(name, 1);
    legacyRequest.onupgradeneeded = () => {
      const database = legacyRequest.result;
      database.createObjectStore("definitions", { keyPath: ["scale_id", "version"] });
      const drafts = database.createObjectStore("drafts", { keyPath: ["owner_id", "scale_id"] });
      drafts.createIndex("owner_id", "owner_id");
      const outbox = database.createObjectStore("outbox", {
        keyPath: ["owner_id", "submission_id"],
      });
      outbox.createIndex("owner_id", "owner_id");
      outbox.createIndex("owner_created", ["owner_id", "created_at"]);
      database.createObjectStore("meta", { keyPath: "key" });
    };
    const legacyDatabase = await new Promise<IDBDatabase>((resolve, reject) => {
      legacyRequest.addEventListener("success", () => resolve(legacyRequest.result), {
        once: true,
      });
      legacyRequest.addEventListener("error", () => reject(legacyRequest.error), { once: true });
    });
    const legacyWrite = legacyDatabase.transaction(["meta", "drafts", "outbox"], "readwrite");
    legacyWrite.objectStore("meta").put({
      key: "owner_pointer",
      value: { id: ownerId, role: "patient", locked: false },
    });
    legacyWrite.objectStore("drafts").put({
      owner_id: ownerId,
      scale_id: "synthetic-scale",
      revision: 3,
      answers: { q1: 1 },
      definition_snapshot: { scale: { id: "synthetic-scale", version: 1 } },
      updated_at: "2026-10-22T10:00:00.000Z",
    });
    legacyWrite.objectStore("outbox").put({
      owner_id: ownerId,
      submission_id: "00000000-0000-4000-8000-000000000011",
      payload: { patient_id: ownerId, submission_id: "00000000-0000-4000-8000-000000000011" },
      definition_snapshot: { scale: { id: "synthetic-scale", version: 1 } },
      state: "pending",
      last_error: null,
      created_at: "2026-10-22T10:00:00.000Z",
    });
    await new Promise<void>((resolve, reject) => {
      legacyWrite.addEventListener("complete", () => resolve(), { once: true });
      legacyWrite.addEventListener("abort", () => reject(legacyWrite.error), { once: true });
    });
    legacyDatabase.close();

    const { openAssessmentDatabase } = await import("/patient/src/assessments/database.ts");
    const database = await openAssessmentDatabase();
    const transaction = database.transaction(["meta", "drafts", "outbox"], "readonly");
    const [pointer, draft, queued] = await Promise.all([
      requestValue(transaction.objectStore("meta").get("owner_pointer")),
      requestValue(transaction.objectStore("drafts").get([ownerId, "synthetic-scale"])),
      requestValue(
        transaction.objectStore("outbox").get([ownerId, "00000000-0000-4000-8000-000000000011"]),
      ),
    ]);
    return {
      version: database.version,
      stores: Array.from(database.objectStoreNames).sort(),
      pointer,
      draft,
      queued,
    };
  });

  expect(result).toEqual({
    version: 2,
    stores: [
      "definitions",
      "diaryAudio",
      "diaryContext",
      "diaryDrafts",
      "diaryEntries",
      "diaryOutbox",
      "drafts",
      "intakeCache",
      "intakeOutbox",
      "meta",
      "outbox",
    ],
    pointer: {
      key: "owner_pointer",
      value: {
        id: "00000000-0000-4000-8000-0000000000a1",
        role: "patient",
        locked: false,
      },
    },
    draft: {
      owner_id: "00000000-0000-4000-8000-0000000000a1",
      scale_id: "synthetic-scale",
      revision: 3,
      answers: { q1: 1 },
      definition_snapshot: { scale: { id: "synthetic-scale", version: 1 } },
      updated_at: "2026-10-22T10:00:00.000Z",
    },
    queued: {
      owner_id: "00000000-0000-4000-8000-0000000000a1",
      submission_id: "00000000-0000-4000-8000-000000000011",
      payload: {
        patient_id: "00000000-0000-4000-8000-0000000000a1",
        submission_id: "00000000-0000-4000-8000-000000000011",
      },
      definition_snapshot: { scale: { id: "synthetic-scale", version: 1 } },
      state: "pending",
      last_error: null,
      created_at: "2026-10-22T10:00:00.000Z",
    },
  });
});

test("restores independent quick drafts alongside the owner's daily draft", async ({ page }) => {
  await openPatientPage(page);
  await page.evaluate(async () => {
    const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
    const assessments = await AssessmentStore.open();
    await assessments.rememberOnlinePatient({
      id: "00000000-0000-4000-8000-0000000000a1",
      role: "patient",
    });
  });

  const saved = await page.evaluate(async () => {
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    const diary = await DiaryStore.open();
    const ownerId = "00000000-0000-4000-8000-0000000000a1";
    const occurredAt = "2026-10-23T10:00:00.000Z";
    await diary.saveDraft({
      ownerId,
      draftId: "quick-text-1",
      expectedRevision: 0,
      mode: "quick",
      entryKind: "text",
      occurredAt,
      step: "story",
      moodLevel: null,
      moodWords: [],
      text: "Первая быстрая запись",
      surveyStatus: "unknown",
    });
    await diary.saveDraft({
      ownerId,
      draftId: "quick-mood-1",
      expectedRevision: 0,
      mode: "quick",
      entryKind: "mood",
      occurredAt,
      step: "mood",
      moodLevel: 4,
      moodWords: ["интерес"],
      text: "",
      surveyStatus: "unknown",
    });
    await diary.saveDraft({
      ownerId,
      draftId: "daily-2026-10-23",
      expectedRevision: 0,
      mode: "daily",
      entryKind: "daily",
      dailyOn: "2026-10-23",
      occurredAt,
      step: "survey",
      moodLevel: 3,
      moodWords: ["спокойствие"],
      text: "Итог дня",
      surveyStatus: "pending",
    });
    return diary.listDrafts(ownerId);
  });

  expect(saved).toHaveLength(3);
  expect(saved.map((draft: { draft_id: string }) => draft.draft_id).sort()).toEqual([
    "daily-2026-10-23",
    "quick-mood-1",
    "quick-text-1",
  ]);

  await page.reload();
  const restored = await page.evaluate(async () => {
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    const diary = await DiaryStore.open();
    return diary.listDrafts("00000000-0000-4000-8000-0000000000a1");
  });

  expect(restored).toHaveLength(3);
  expect(restored).toEqual(saved);
  expect(restored).toContainEqual(
    expect.objectContaining({
      draft_id: "quick-text-1",
      text: "Первая быстрая запись",
      revision: 1,
    }),
  );
  expect(restored).toContainEqual(
    expect.objectContaining({
      draft_id: "quick-mood-1",
      mood_level: 4,
      mood_words: ["интерес"],
      revision: 1,
    }),
  );
  expect(restored).toContainEqual(
    expect.objectContaining({
      draft_id: "daily-2026-10-23",
      daily_on: "2026-10-23",
      step: "survey",
      survey_status: "pending",
      revision: 1,
    }),
  );
});

test("queues one durable entry and replays its immutable command ID", async ({ page }) => {
  await openPatientPage(page);
  await page.evaluate(async () => {
    const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
    await (await AssessmentStore.open()).rememberOnlinePatient({
      id: "00000000-0000-4000-8000-0000000000a1",
      role: "patient",
    });
  });

  const result = await page.evaluate(async () => {
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    const store = await DiaryStore.open();
    const ownerId = "00000000-0000-4000-8000-0000000000a1";
    const payload = {
      entry_id: "00000000-0000-4000-8000-0000000000b1",
      operation_id: "00000000-0000-4000-8000-0000000000c1",
      kind: "text" as const,
      occurred_at: "2026-10-23T10:00:00.000Z",
      text: "Сохраняю рассказ",
      mood_level: null,
      mood_words: [],
    };
    const first = await store.enqueueEntry({ ownerId, payload });
    const replay = await store.enqueueEntry({ ownerId, payload });
    return {
      first,
      replay,
      entries: await store.listEntries(ownerId),
      commands: await store.getOutboxForOwner(ownerId),
    };
  });

  expect(result.first.status).toBe("queued");
  expect(result.replay.status).toBe("replayed");
  expect(result.entries).toHaveLength(1);
  expect(result.commands).toHaveLength(1);
  expect(result.entries[0]).toMatchObject({
    owner_id: "00000000-0000-4000-8000-0000000000a1",
    entry_id: "00000000-0000-4000-8000-0000000000b1",
    text: "Сохраняю рассказ",
    sync_state: "pending",
  });
  expect(result.commands[0]).toMatchObject({
    operation_id: "00000000-0000-4000-8000-0000000000c1",
    entry_id: "00000000-0000-4000-8000-0000000000b1",
    payload: { text: "Сохраняю рассказ" },
    state: "pending",
  });
});

test("preserves both payloads when a local retry reuses an operation ID with changed data", async ({
  page,
}) => {
  await openPatientPage(page);
  await page.evaluate(async () => {
    const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
    await (await AssessmentStore.open()).rememberOnlinePatient({
      id: "00000000-0000-4000-8000-0000000000a1",
      role: "patient",
    });
  });

  const result = await page.evaluate(async () => {
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    const store = await DiaryStore.open();
    const ownerId = "00000000-0000-4000-8000-0000000000a1";
    const original = {
      entry_id: "00000000-0000-4000-8000-0000000000b4",
      operation_id: "00000000-0000-4000-8000-0000000000c4",
      kind: "text" as const,
      occurred_at: "2026-10-23T10:00:00.000Z",
      text: "Исходное содержимое",
    };
    const changed = { ...original, text: "Изменённое содержимое" };
    await store.enqueueEntry({ ownerId, payload: original });
    const conflict = await store.enqueueEntry({ ownerId, payload: changed });
    return {
      conflict,
      entry: (await store.listEntries(ownerId))[0],
      command: (await store.getOutboxForOwner(ownerId))[0],
    };
  });

  expect(result.conflict.status).toBe("conflict");
  expect(result.entry).toMatchObject({ text: "Исходное содержимое", sync_state: "conflict" });
  expect(result.command).toMatchObject({
    payload: { text: "Исходное содержимое" },
    payload_conflicts: [{ text: "Изменённое содержимое" }],
    state: "conflict",
  });
});
