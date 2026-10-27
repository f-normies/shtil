import type { Schemas } from "@compass/api-client";
import { expect, test } from "@playwright/test";
import { mockAssessmentApi } from "./assessment-api-mock";
import { DEFINITION_FORM, PATIENT_A, SCALE_ID } from "./assessment-fixtures";
import { mockAssessmentAuth, openPatientPage, storeCall } from "./assessment-test-helpers";

test("final review regression: an intermediate autosave cannot replace newer form answers", async ({
  page,
}) => {
  const definition: Schemas["ScaleDefinitionOut"] = {
    ...DEFINITION_FORM,
    scale: {
      ...DEFINITION_FORM.scale,
      items: [
        ...DEFINITION_FORM.scale.items,
        { id: "third", text: "Third question", options: [{ value: 1, text: "Yes" }] },
      ],
    },
  };
  await mockAssessmentApi(page.context(), { definitions: [definition] });
  await page.goto(`/patient/assessments/${SCALE_ID}`);
  const calm = page.getByRole("group", { name: "I felt calm." });
  const rest = page.getByRole("group", { name: "I could rest." });
  const third = page.getByRole("group", { name: "Third question" });

  await page.evaluate(async () => {
    type TestWindow = Window & {
      __saveGates?: Record<number, () => void>;
      __saveCalls?: number;
    };
    const testWindow = window as TestWindow;
    testWindow.__saveGates = {};
    testWindow.__saveCalls = 0;
    const { getAssessmentStore } = await import("/patient/src/session.ts");
    const store = await getAssessmentStore();
    const saveDraft = store.saveDraft.bind(store);
    store.saveDraft = async (input) => {
      const call = (testWindow.__saveCalls ?? 0) + 1;
      testWindow.__saveCalls = call;
      if (call <= 2) {
        await new Promise<void>((resolve) => {
          let gates = testWindow.__saveGates;
          if (!gates) {
            gates = {};
            testWindow.__saveGates = gates;
          }
          gates[call] = resolve;
        });
      }
      return saveDraft(input);
    };
  });

  await calm.getByRole("radio", { name: "Sometimes" }).check();
  await page.waitForFunction(() =>
    Boolean((window as Window & { __saveGates?: Record<number, () => void> }).__saveGates?.[1]),
  );
  await rest.getByRole("radio", { name: "Often" }).check();
  await page.evaluate(() => {
    const release = (window as Window & { __saveGates?: Record<number, () => void> })
      .__saveGates?.[1];
    if (!release) throw new Error("First autosave gate was not installed");
    release();
  });
  await page.waitForFunction(() =>
    Boolean((window as Window & { __saveGates?: Record<number, () => void> }).__saveGates?.[2]),
  );

  await third.getByRole("radio", { name: "Yes" }).check();
  await page.evaluate(() => {
    const release = (window as Window & { __saveGates?: Record<number, () => void> })
      .__saveGates?.[2];
    if (!release) throw new Error("Second autosave gate was not installed");
    release();
  });
  await expect(page.getByText("Ответы сохранены на устройстве.", { exact: true })).toBeVisible();
  const draft = await storeCall<Record<string, unknown> | null>(page, "getDraft", {
    ownerId: PATIENT_A,
    scaleId: SCALE_ID,
  });
  expect(draft).toMatchObject({
    revision: 3,
    answers: { calm: 2, rest: 4, third: 1 },
  });
});

test("final review regression: deleting a draft never reuses its revision for stale actions", async ({
  page,
}) => {
  await mockAssessmentAuth(page.context());
  await openPatientPage(page);

  const result = await page.evaluate(
    async ({ ownerId, baseDefinition }) => {
      const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
      const store = await AssessmentStore.open();
      await store.rememberOnlinePatient({ id: ownerId, role: "patient" });
      const answers = { calm: 2, rest: 4 };
      const scenarios = [
        { scaleId: "synthetic-stale-save", staleAction: "save", lifecycle: "enqueue" },
        { scaleId: "synthetic-stale-discard", staleAction: "discard", lifecycle: "enqueue" },
        { scaleId: "synthetic-stale-enqueue", staleAction: "enqueue", lifecycle: "enqueue" },
        {
          scaleId: "synthetic-stale-old-version",
          staleAction: "save",
          lifecycle: "old-version-discard",
        },
        { scaleId: "synthetic-stale-restore", staleAction: "save", lifecycle: "restore" },
        {
          scaleId: "synthetic-stale-clear-owner-data",
          staleAction: "discard",
          lifecycle: "clear-owner-data",
        },
      ] as const;
      const results = [];

      for (const [index, scenario] of scenarios.entries()) {
        const definitionSnapshot = {
          ...baseDefinition,
          scale: { ...baseDefinition.scale, id: scenario.scaleId },
        };
        await store.cacheDefinition(definitionSnapshot);
        const first = await store.saveDraft({
          ownerId,
          scaleId: scenario.scaleId,
          expectedRevision: 0,
          answers,
          definitionSnapshot,
        });
        if (first.status !== "saved") throw new Error("Initial draft was not saved");
        const originalSubmission = {
          patient_id: ownerId,
          submission_id: `00000000-0000-4000-8000-00000000001${index}`,
          scale_id: scenario.scaleId,
          scale_version: definitionSnapshot.scale.version,
          completed_at: "2026-10-17T10:00:00.000Z",
          answers,
        };
        let freshRevision: number;
        let expectedOutboxCount = 0;
        if (scenario.lifecycle === "old-version-discard") {
          await store.discardDraft({
            ownerId,
            scaleId: scenario.scaleId,
            expectedRevision: first.draft.revision,
          });
          const currentVersion = {
            ...definitionSnapshot,
            scale: { ...definitionSnapshot.scale, version: definitionSnapshot.scale.version + 1 },
          };
          await store.cacheDefinition(currentVersion);
          const fresh = await store.saveDraft({
            ownerId,
            scaleId: scenario.scaleId,
            expectedRevision: 0,
            answers,
            definitionSnapshot: currentVersion,
          });
          if (fresh.status !== "saved") throw new Error("Current-version draft was not saved");
          freshRevision = fresh.draft.revision;
        } else if (scenario.lifecycle === "clear-owner-data") {
          const { openAssessmentDatabase, requestResult, runTransaction } = await import(
            "/patient/src/assessments/database.ts"
          );
          const database = await openAssessmentDatabase();
          const revisionKey = `draft_revision:${JSON.stringify([ownerId, scenario.scaleId])}`;
          await runTransaction(database, ["meta"], "readwrite", (transaction) =>
            requestResult(transaction.objectStore("meta").delete(revisionKey)),
          );
          await store.clearOwnerDataAfterConfirmation(ownerId);
          await store.rememberOnlinePatient({ id: ownerId, role: "patient" });
          const fresh = await store.saveDraft({
            ownerId,
            scaleId: scenario.scaleId,
            expectedRevision: 0,
            answers,
            definitionSnapshot,
          });
          if (fresh.status !== "saved") throw new Error("Cleared draft was not recreated");
          freshRevision = fresh.draft.revision;
        } else {
          const queued = await store.enqueueDraft({
            ownerId,
            scaleId: scenario.scaleId,
            expectedRevision: first.draft.revision,
            payload: originalSubmission,
          });
          if (queued.status !== "queued") throw new Error("Initial draft was not enqueued");
          if (scenario.lifecycle === "enqueue") expectedOutboxCount = 1;

          if (scenario.lifecycle === "restore") {
            await store.blockOutboxRecord(ownerId, originalSubmission.submission_id, {
              status: 422,
              code: "invalid_answers",
            });
            const restored = await store.restoreOutboxToDraft(
              ownerId,
              originalSubmission.submission_id,
            );
            if (restored.status !== "restored") throw new Error("Queued draft was not restored");
            freshRevision = restored.draft.revision;
          } else {
            const fresh = await store.saveDraft({
              ownerId,
              scaleId: scenario.scaleId,
              expectedRevision: 0,
              answers,
              definitionSnapshot,
            });
            if (fresh.status !== "saved") throw new Error("Recreated draft was not saved");
            freshRevision = fresh.draft.revision;
          }
        }
        const stalePayload = {
          ...originalSubmission,
          submission_id: `00000000-0000-4000-8000-00000000002${index}`,
        };
        let staleResult: unknown;
        if (scenario.staleAction === "save") {
          staleResult = await store.saveDraft({
            ownerId,
            scaleId: scenario.scaleId,
            expectedRevision: first.draft.revision,
            answers: { calm: 5, rest: 4 },
            definitionSnapshot,
          });
        } else if (scenario.staleAction === "discard") {
          staleResult = await store.discardDraft({
            ownerId,
            scaleId: scenario.scaleId,
            expectedRevision: first.draft.revision,
          });
        } else {
          staleResult = await store.enqueueDraft({
            ownerId,
            scaleId: scenario.scaleId,
            expectedRevision: first.draft.revision,
            payload: stalePayload,
          });
        }
        results.push({
          action: scenario.staleAction,
          lifecycle: scenario.lifecycle,
          expectedOutboxCount,
          firstRevision: first.draft.revision,
          freshRevision,
          staleResult,
          currentDraft: await store.getDraft({ ownerId, scaleId: scenario.scaleId }),
          outbox: (await store.getOutboxForOwner(ownerId)).filter(
            (record) => record.payload.scale_id === scenario.scaleId,
          ),
        });
      }
      return results;
    },
    { ownerId: PATIENT_A, baseDefinition: DEFINITION_FORM },
  );

  for (const scenario of result) {
    expect(scenario.freshRevision).toBeGreaterThan(scenario.firstRevision);
    expect(scenario.staleResult).toMatchObject({ status: "conflict" });
    expect(scenario.currentDraft).toMatchObject({
      revision: scenario.freshRevision,
      answers: { calm: 2, rest: 4 },
      definition_snapshot: {
        scale: { version: scenario.lifecycle === "old-version-discard" ? 2 : 1 },
      },
    });
    expect(scenario.outbox).toHaveLength(scenario.expectedOutboxCount);
  }
});

test("final review regression: a failed 401 lock denies offline data without deleting the queue", async ({
  page,
}) => {
  const api = await mockAssessmentApi(page.context());
  page.on("response", (response) => {
    if (
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/v1/assessments" &&
      response.status() === 401
    ) {
      api.setAuthenticatedUser(null);
      api.setNetworkAvailable(false);
    }
  });
  await page.goto("/patient/");
  await page.getByRole("link", { name: "Заполнить" }).click();
  await page
    .getByRole("group", { name: "I felt calm." })
    .getByRole("radio", { name: "Sometimes" })
    .check();
  await page
    .getByRole("group", { name: "I could rest." })
    .getByRole("radio", { name: "Often" })
    .check();
  await expect(page.getByText("Ответы сохранены на устройстве.", { exact: true })).toBeVisible();
  await page.evaluate(async () => {
    const { getAssessmentStore } = await import("/patient/src/session.ts");
    const store = await getAssessmentStore();
    store.lockOwnerPointer = async () => {
      throw new DOMException("Synthetic IndexedDB lock failure", "UnknownError");
    };
  });
  api.setNextSubmissionResponse(401, "unauthenticated");

  await page.getByRole("button", { name: "Отправить ответы" }).click();
  await expect(page.getByLabel("Электронная почта", { exact: true })).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("Не удалось сохранить блокировку доступа");
  await expect(page.getByRole("heading", { name: "Ожидают отправки" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: /Здравствуйте/ })).toHaveCount(0);
  expect(
    await storeCall<Record<string, unknown>[]>(page, "getOutboxForOwner", PATIENT_A),
  ).toMatchObject([
    {
      owner_id: PATIENT_A,
      payload: { patient_id: PATIENT_A, answers: { calm: 2, rest: 4 } },
    },
  ]);
});
