import { expect, test } from "@playwright/test";
import { DEFINITION_V1, PATIENT_A, payload, SCALE_ID, SUBMISSION_A } from "./assessment-fixtures";
import { mockAssessmentAuth, storeCall } from "./assessment-test-helpers";

test.beforeEach(async ({ context }) => mockAssessmentAuth(context));

test("a blocked validation response returns to a draft with its original definition atomically", async ({
  page,
}) => {
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
  await storeCall(page, "blockOutboxRecord", PATIENT_A, SUBMISSION_A, {
    status: 422,
    code: "invalid_answers",
  });

  const restored = await page.evaluate(
    async ({ ownerId, submissionId }) => {
      const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
      const store = await AssessmentStore.open();
      const restore = (
        store as unknown as {
          restoreOutboxToDraft: (owner: string, submission: string) => Promise<unknown>;
        }
      ).restoreOutboxToDraft;
      return restore.call(store, ownerId, submissionId);
    },
    { ownerId: PATIENT_A, submissionId: SUBMISSION_A },
  );

  expect(restored).toMatchObject({
    status: "restored",
    draft: {
      owner_id: PATIENT_A,
      scale_id: SCALE_ID,
      revision: 2,
      answers: { q1: 0 },
      definition_snapshot: DEFINITION_V1,
    },
  });
  expect(await storeCall(page, "getOutboxForOwner", PATIENT_A)).toEqual([]);
  expect(
    await storeCall(page, "getDraft", { ownerId: PATIENT_A, scaleId: SCALE_ID }),
  ).toMatchObject({
    revision: 2,
    answers: { q1: 0 },
    definition_snapshot: DEFINITION_V1,
  });
});
