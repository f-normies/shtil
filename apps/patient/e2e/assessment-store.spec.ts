import { expect, test } from "@playwright/test";
import {
  DEFINITION_TWO_ITEMS,
  DEFINITION_V1,
  DEFINITION_V2,
  PATIENT_A,
  PATIENT_B,
  payload,
  SCALE_ID,
  SUBMISSION_A,
  SUBMISSION_B,
} from "./assessment-fixtures";
import {
  mockAssessmentAuth,
  openPatientPage,
  seedOneQueuedAnswer,
  storeCall,
} from "./assessment-test-helpers";

test.beforeEach(async ({ context }) => mockAssessmentAuth(context));

test("cached definitions can be enumerated for offline catalog access", async ({ page }) => {
  await openPatientPage(page);
  await storeCall(page, "cacheDefinition", DEFINITION_V1);
  await storeCall(page, "cacheDefinition", DEFINITION_V2);

  const definitions = await page.evaluate(async () => {
    const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
    const store = await AssessmentStore.open();
    const operation = (store as unknown as { getCachedDefinitions: () => Promise<unknown[]> })
      .getCachedDefinitions;
    return operation.call(store);
  });

  expect(definitions).toEqual([DEFINITION_V1, DEFINITION_V2]);
});

test("a saved draft survives reload and queued answers retain the definition they used", async ({
  page,
}) => {
  await openPatientPage(page);
  await storeCall(page, "rememberOnlinePatient", { id: PATIENT_A, role: "patient" });
  await storeCall(page, "cacheDefinition", DEFINITION_V1);

  const saved = await storeCall<{ status: string; draft: { revision: number } }>(
    page,
    "saveDraft",
    {
      ownerId: PATIENT_A,
      scaleId: SCALE_ID,
      expectedRevision: 0,
      answers: { q1: 0 },
      definitionSnapshot: DEFINITION_V1,
    },
  );
  expect(saved).toMatchObject({ status: "saved", draft: { revision: 1 } });

  await page.reload();
  const owner = await storeCall(page, "getOwnerPointer");
  const persistedDraft = await storeCall(page, "getDraft", {
    ownerId: PATIENT_A,
    scaleId: SCALE_ID,
  });
  expect(owner).toEqual({ id: PATIENT_A, role: "patient", locked: false });
  expect(persistedDraft).toMatchObject({ revision: 1, answers: { q1: 0 } });

  const queued = await storeCall(page, "enqueueDraft", {
    ownerId: PATIENT_A,
    scaleId: SCALE_ID,
    expectedRevision: 1,
    payload: payload(),
  });
  await storeCall(page, "cacheDefinition", DEFINITION_V2);
  const records = await storeCall<Array<Record<string, unknown>>>(
    page,
    "getOutboxForOwner",
    PATIENT_A,
  );

  expect(queued).toMatchObject({ status: "queued", record: { payload: payload() } });
  expect(records).toHaveLength(1);
  expect(records[0]).toMatchObject({
    owner_id: PATIENT_A,
    submission_id: SUBMISSION_A,
    payload: payload(),
    definition_snapshot: DEFINITION_V1,
    state: "pending",
  });
  expect(await storeCall(page, "getDraft", { ownerId: PATIENT_A, scaleId: SCALE_ID })).toBeNull();
});

test("two tabs reject a stale save and stale enqueue without replacing the newer draft", async ({
  page,
}) => {
  await openPatientPage(page);
  await storeCall(page, "rememberOnlinePatient", { id: PATIENT_A, role: "patient" });
  await storeCall(page, "cacheDefinition", DEFINITION_V1);
  await storeCall(page, "saveDraft", {
    ownerId: PATIENT_A,
    scaleId: SCALE_ID,
    expectedRevision: 0,
    answers: { q1: 0 },
    definitionSnapshot: DEFINITION_V1,
  });

  const secondTab = await page.context().newPage();
  await openPatientPage(secondTab);
  const staleTabDraft = await storeCall<{ revision: number }>(secondTab, "getDraft", {
    ownerId: PATIENT_A,
    scaleId: SCALE_ID,
  });
  expect(staleTabDraft.revision).toBe(1);

  await storeCall(page, "saveDraft", {
    ownerId: PATIENT_A,
    scaleId: SCALE_ID,
    expectedRevision: 1,
    answers: { q1: 1 },
    definitionSnapshot: DEFINITION_V1,
  });

  const staleSave = await storeCall<{
    status: string;
    currentDraft: { revision: number; answers: object };
  }>(secondTab, "saveDraft", {
    ownerId: PATIENT_A,
    scaleId: SCALE_ID,
    expectedRevision: staleTabDraft.revision,
    answers: { q1: 0 },
    definitionSnapshot: DEFINITION_V1,
  });
  const staleEnqueue = await storeCall(secondTab, "enqueueDraft", {
    ownerId: PATIENT_A,
    scaleId: SCALE_ID,
    expectedRevision: staleTabDraft.revision,
    payload: payload(PATIENT_A, SUBMISSION_B),
  });

  expect(staleSave).toMatchObject({
    status: "conflict",
    currentDraft: { revision: 2, answers: { q1: 1 } },
  });
  expect(staleEnqueue).toMatchObject({ status: "conflict" });
  expect(await storeCall(secondTab, "getOutboxForOwner", PATIENT_A)).toEqual([]);
  expect(
    await storeCall(secondTab, "getDraft", { ownerId: PATIENT_A, scaleId: SCALE_ID }),
  ).toMatchObject({
    revision: 2,
    answers: { q1: 1 },
  });
  await secondTab.close();
});

test("an IndexedDB abort after a successful add leaves the draft and queue unchanged", async ({
  page,
}) => {
  await openPatientPage(page);
  await storeCall(page, "rememberOnlinePatient", { id: PATIENT_A, role: "patient" });
  await storeCall(page, "saveDraft", {
    ownerId: PATIENT_A,
    scaleId: SCALE_ID,
    expectedRevision: 0,
    answers: { q1: 0 },
    definitionSnapshot: DEFINITION_V1,
  });

  await page.evaluate(() => {
    const objectStore = IDBObjectStore.prototype;
    const nativeAdd = objectStore.add;
    let shouldAbort = true;
    objectStore.add = function (...args) {
      const request = Reflect.apply(nativeAdd, this, args) as IDBRequest;
      if (shouldAbort) {
        shouldAbort = false;
        request.addEventListener("success", () => request.transaction?.abort(), { once: true });
      }
      return request;
    };
  });

  await expect(
    storeCall(page, "enqueueDraft", {
      ownerId: PATIENT_A,
      scaleId: SCALE_ID,
      expectedRevision: 1,
      payload: payload(),
    }),
  ).rejects.toThrow(/abort/i);

  expect(
    await storeCall(page, "getDraft", { ownerId: PATIENT_A, scaleId: SCALE_ID }),
  ).toMatchObject({
    revision: 1,
    answers: { q1: 0 },
  });
  expect(await storeCall(page, "getOutboxForOwner", PATIENT_A)).toEqual([]);
});

test("enqueue rejects answers that differ from the saved draft without losing it", async ({
  page,
}) => {
  await openPatientPage(page);
  await storeCall(page, "rememberOnlinePatient", { id: PATIENT_A, role: "patient" });
  await storeCall(page, "saveDraft", {
    ownerId: PATIENT_A,
    scaleId: SCALE_ID,
    expectedRevision: 0,
    answers: { q1: 1 },
    definitionSnapshot: DEFINITION_V1,
  });

  await expect(
    storeCall(page, "enqueueDraft", {
      ownerId: PATIENT_A,
      scaleId: SCALE_ID,
      expectedRevision: 1,
      payload: payload(PATIENT_A, SUBMISSION_A, { q1: 0 }),
    }),
  ).rejects.toThrow(/answer/i);

  expect(await storeCall(page, "getOutboxForOwner", PATIENT_A)).toEqual([]);
  expect(
    await storeCall(page, "getDraft", { ownerId: PATIENT_A, scaleId: SCALE_ID }),
  ).toMatchObject({
    revision: 1,
    answers: { q1: 1 },
  });
});

test("enqueue snapshots its payload before awaiting and ignores answer key order", async ({
  page,
}) => {
  await openPatientPage(page);
  await storeCall(page, "rememberOnlinePatient", { id: PATIENT_A, role: "patient" });
  await storeCall(page, "saveDraft", {
    ownerId: PATIENT_A,
    scaleId: SCALE_ID,
    expectedRevision: 0,
    answers: { q1: 0, q2: 1 },
    definitionSnapshot: DEFINITION_TWO_ITEMS,
  });

  const result = await page.evaluate(
    async ({ ownerId, submission }) => {
      const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
      const store = await AssessmentStore.open();
      const pending = store.enqueueDraft({
        ownerId,
        scaleId: submission.scale_id,
        expectedRevision: 1,
        payload: submission,
      });
      submission.answers.q1 = 1;
      return pending;
    },
    {
      ownerId: PATIENT_A,
      submission: payload(PATIENT_A, SUBMISSION_A, { q2: 1, q1: 0 }),
    },
  );

  expect(result).toMatchObject({
    status: "queued",
    record: { payload: payload(PATIENT_A, SUBMISSION_A, { q1: 0, q2: 1 }) },
  });
  expect(await storeCall(page, "getOutboxForOwner", PATIENT_A)).toMatchObject([
    { payload: payload(PATIENT_A, SUBMISSION_A, { q1: 0, q2: 1 }) },
  ]);
});

test("a cached definition version is immutable while an older cached version stays usable", async ({
  page,
}) => {
  await openPatientPage(page);
  await storeCall(page, "rememberOnlinePatient", { id: PATIENT_A, role: "patient" });
  await storeCall(page, "cacheDefinition", DEFINITION_V1);
  await storeCall(page, "saveDraft", {
    ownerId: PATIENT_A,
    scaleId: SCALE_ID,
    expectedRevision: 0,
    answers: { q1: 0 },
    definitionSnapshot: DEFINITION_V1,
  });
  await storeCall(page, "cacheDefinition", DEFINITION_V2);

  const validOldVersionSave = await storeCall<{ status: string }>(page, "saveDraft", {
    ownerId: PATIENT_A,
    scaleId: SCALE_ID,
    expectedRevision: 1,
    answers: { q1: 1 },
    definitionSnapshot: DEFINITION_V1,
  });
  expect(validOldVersionSave.status).toBe("saved");

  const changedDefinition = {
    ...DEFINITION_V1,
    scale: { ...DEFINITION_V1.scale, instruction: "Changed content in the same version" },
  };
  await expect(
    storeCall(page, "saveDraft", {
      ownerId: PATIENT_A,
      scaleId: SCALE_ID,
      expectedRevision: 2,
      answers: { q1: 0 },
      definitionSnapshot: changedDefinition,
    }),
  ).rejects.toThrow(/definition/i);

  expect(
    await storeCall(page, "getDraft", { ownerId: PATIENT_A, scaleId: SCALE_ID }),
  ).toMatchObject({
    revision: 2,
    answers: { q1: 1 },
    definition_snapshot: DEFINITION_V1,
  });
});

test("account switching hides other owners and confirmed deletion clears only the current owner", async ({
  page,
}) => {
  await openPatientPage(page);
  await seedOneQueuedAnswer(page, PATIENT_A, SUBMISSION_A);
  await storeCall(page, "rememberOnlinePatient", { id: PATIENT_B, role: "patient" });
  await storeCall(page, "saveDraft", {
    ownerId: PATIENT_B,
    scaleId: SCALE_ID,
    expectedRevision: 0,
    answers: { q1: 1 },
    definitionSnapshot: DEFINITION_V1,
  });

  await expect(
    storeCall(page, "getDraft", { ownerId: PATIENT_A, scaleId: SCALE_ID }),
  ).rejects.toThrow();
  await expect(storeCall(page, "getOutboxForOwner", PATIENT_A)).rejects.toThrow();
  await expect(storeCall(page, "clearOwnerDataAfterConfirmation", PATIENT_A)).rejects.toThrow();

  await storeCall(page, "clearOwnerDataAfterConfirmation", PATIENT_B);
  expect(await storeCall(page, "getOwnerPointer")).toBeNull();
  await storeCall(page, "rememberOnlinePatient", { id: PATIENT_A, role: "patient" });
  expect(await storeCall(page, "getDraft", { ownerId: PATIENT_A, scaleId: SCALE_ID })).toBeNull();
  expect(await storeCall(page, "getOutboxForOwner", PATIENT_A)).toHaveLength(1);
});

test("local data summary counts drafts and queued answers for logout confirmation", async ({
  page,
}) => {
  // Stay on the login route so its /auth/me check cannot race and lock this
  // deliberately seeded owner pointer as an unauthenticated home-page visit.
  await page.goto("/patient/login");
  await expect(page.getByLabel("Электронная почта", { exact: true })).toBeVisible();
  await storeCall(page, "rememberOnlinePatient", { id: PATIENT_A, role: "patient" });
  await storeCall(page, "cacheDefinition", DEFINITION_V1);
  await storeCall(page, "saveDraft", {
    ownerId: PATIENT_A,
    scaleId: SCALE_ID,
    expectedRevision: 0,
    answers: { q1: 0 },
    definitionSnapshot: DEFINITION_V1,
  });
  await storeCall(page, "enqueueDraft", {
    ownerId: PATIENT_A,
    scaleId: SCALE_ID,
    expectedRevision: 1,
    payload: payload(PATIENT_A, SUBMISSION_A),
  });
  await storeCall(page, "saveDraft", {
    ownerId: PATIENT_A,
    scaleId: SCALE_ID,
    expectedRevision: 0,
    answers: { q1: 0 },
    definitionSnapshot: DEFINITION_V1,
  });

  const counts = await page.evaluate(async (ownerId) => {
    const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
    const store = await AssessmentStore.open();
    const readCounts = (
      store as unknown as {
        getOwnerDataCounts: (
          id: string,
        ) => Promise<{ drafts: number; outbox: number; audio: number }>;
      }
    ).getOwnerDataCounts;
    return readCounts.call(store, ownerId);
  }, PATIENT_A);

  expect(counts).toEqual({ drafts: 1, outbox: 1, audio: 0 });
});
