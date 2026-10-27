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

test("a persisted blocked or conflicted command fences later commands on the next sync", async ({
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
    const entryIds = [
      "00000000-0000-4000-8000-0000000000b6",
      "00000000-0000-4000-8000-0000000000b7",
    ];
    for (const [index, entryId] of entryIds.entries()) {
      const createId = `00000000-0000-4000-8000-0000000000d${index + 1}`;
      await diary.enqueueEntry({
        ownerId,
        payload: {
          entry_id: entryId,
          operation_id: createId,
          kind: "text",
          occurred_at: `2026-10-23T10:0${index}:00.000Z`,
          text: `Запись ${index}`,
        },
      });
      await diary.enqueueUpdate({
        ownerId,
        entryId,
        payload: {
          operation_id: `00000000-0000-4000-8000-0000000000d${index + 3}`,
          expected_revision: 1,
          text: `Изменение ${index}`,
        },
      });
      await diary.blockCommand({
        ownerId,
        operationId: createId,
        error:
          index === 0 ? { status: 422, code: "invalid_entry" } : { status: 409, code: "conflict" },
      });
    }

    const calls: string[] = [];
    const engine = createDiarySyncEngine(diary, assessments, {
      getCurrentUser: async () => ({ id: ownerId, role: "patient" }),
      getSessionGeneration: () => 7,
      createEntry: async (payload) => {
        calls.push(`create:${payload.entry_id}`);
        return { status: 201 };
      },
      updateEntry: async (entryId) => {
        calls.push(`update:${entryId}`);
        return { status: 200 };
      },
    });
    const outcome = await engine.sync(ownerId, 7);
    const commands = await diary.getOutboxForOwner(ownerId);
    return {
      calls,
      outcome,
      entries: entryIds.map((entryId) => ({
        entryId,
        commands: commands
          .filter((command) => command.entry_id === entryId)
          .map(({ action, state }) => ({ action, state })),
      })),
    };
  }, OWNER_ID);

  expect(result.calls).toEqual([]);
  expect(result.outcome).toMatchObject({ status: "complete", submitted: 0 });
  expect(result.entries).toEqual([
    {
      entryId: "00000000-0000-4000-8000-0000000000b6",
      commands: [
        { action: "create", state: "blocked" },
        { action: "update", state: "pending" },
      ],
    },
    {
      entryId: "00000000-0000-4000-8000-0000000000b7",
      commands: [
        { action: "create", state: "conflict" },
        { action: "update", state: "pending" },
      ],
    },
  ]);
});

test("reusing an update operation ID for another entry is a local conflict", async ({ page }) => {
  await openPatientPage(page);
  await rememberOwner(page);

  const result = await page.evaluate(async (ownerId) => {
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    const diary = await DiaryStore.open();
    const firstEntry = "00000000-0000-4000-8000-0000000000b9";
    const secondEntry = "00000000-0000-4000-8000-0000000000ba";
    await diary.enqueueEntry({
      ownerId,
      payload: {
        entry_id: firstEntry,
        operation_id: "00000000-0000-4000-8000-0000000000d7",
        kind: "text",
        occurred_at: "2026-10-23T10:00:00.000Z",
        text: "Первая запись",
      },
    });
    await diary.enqueueEntry({
      ownerId,
      payload: {
        entry_id: secondEntry,
        operation_id: "00000000-0000-4000-8000-0000000000d8",
        kind: "text",
        occurred_at: "2026-10-23T10:01:00.000Z",
        text: "Вторая запись",
      },
    });
    const payload = {
      operation_id: "00000000-0000-4000-8000-0000000000d9",
      expected_revision: 1,
      text: "Одинаковое тело",
    };
    await diary.enqueueUpdate({ ownerId, entryId: firstEntry, payload });
    const replay = await diary.enqueueUpdate({ ownerId, entryId: secondEntry, payload });
    return { replay, commands: await diary.getOutboxForOwner(ownerId) };
  }, OWNER_ID);

  expect(result.replay.status).toBe("conflict");
  expect(result.replay.command).toMatchObject({
    action: "update",
    entry_id: "00000000-0000-4000-8000-0000000000b9",
    state: "conflict",
  });
  expect(result.commands).toHaveLength(3);
  expect(result.commands[2]).toMatchObject({
    action: "update",
    entry_id: "00000000-0000-4000-8000-0000000000b9",
  });
});
