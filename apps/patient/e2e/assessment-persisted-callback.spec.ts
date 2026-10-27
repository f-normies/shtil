import { expect, test } from "@playwright/test";
import { DEFINITION_V1, PATIENT_A, payload, SCALE_ID, SUBMISSION_A } from "./assessment-fixtures";
import { mockAssessmentAuth, openPatientPage, storeCall } from "./assessment-test-helpers";

test.beforeEach(async ({ context }) => mockAssessmentAuth(context));

test("assessment persisted callback observes the committed IndexedDB transaction", async ({
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

  const result = await page.evaluate(
    async ({ ownerId, submission }) => {
      const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
      const { persistAssessmentDraft } = await import(
        "/patient/src/assessments/persisted-callback.ts"
      );
      const store = await AssessmentStore.open();
      const databasePrototype = IDBDatabase.prototype as unknown as {
        transaction: (...args: unknown[]) => IDBTransaction;
      };
      const originalTransaction = databasePrototype.transaction;
      let outboxTransactionCommitted = false;
      let commitWasObservedByCallback = false;
      let callbackCount = 0;
      databasePrototype.transaction = function (this: IDBDatabase, ...args: unknown[]) {
        const transaction = Reflect.apply(originalTransaction, this, args) as IDBTransaction;
        const names = typeof args[0] === "string" ? [args[0]] : (args[0] as string[]);
        if (args[1] === "readwrite" && names.includes("outbox")) {
          transaction.addEventListener(
            "complete",
            () => {
              outboxTransactionCommitted = true;
            },
            { once: true },
          );
        }
        return transaction;
      };
      try {
        const saved = await persistAssessmentDraft(
          store,
          {
            ownerId,
            scaleId: submission.scale_id,
            expectedRevision: 1,
            payload: submission,
          },
          () => {
            callbackCount += 1;
            commitWasObservedByCallback = outboxTransactionCommitted;
          },
        );
        return {
          saved,
          callbackCount,
          commitWasObservedByCallback,
          outbox: await store.getOutboxForOwner(ownerId),
        };
      } finally {
        databasePrototype.transaction = originalTransaction;
      }
    },
    { ownerId: PATIENT_A, submission: payload(PATIENT_A, SUBMISSION_A) },
  );

  expect(result.saved).toMatchObject({ status: "queued" });
  expect(result.callbackCount).toBe(1);
  expect(result.commitWasObservedByCallback).toBe(true);
  expect(result.outbox).toMatchObject([{ submission_id: SUBMISSION_A }]);
});

test("assessment persisted callback stays silent when the actual IndexedDB transaction aborts", async ({
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

  const result = await page.evaluate(
    async ({ ownerId, submission }) => {
      const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
      const { persistAssessmentDraft } = await import(
        "/patient/src/assessments/persisted-callback.ts"
      );
      const store = await AssessmentStore.open();
      const prototype = IDBObjectStore.prototype as unknown as {
        add: (this: IDBObjectStore, ...args: unknown[]) => IDBRequest;
      };
      const originalAdd = prototype.add;
      let callbackCount = 0;
      let failure = "";
      prototype.add = function (this: IDBObjectStore, ...args: unknown[]) {
        const request = Reflect.apply(originalAdd, this, args) as IDBRequest;
        if (this.name === "outbox") {
          request.addEventListener("success", () => request.transaction?.abort(), { once: true });
        }
        return request;
      };
      try {
        await persistAssessmentDraft(
          store,
          {
            ownerId,
            scaleId: submission.scale_id,
            expectedRevision: 1,
            payload: submission,
          },
          () => {
            callbackCount += 1;
          },
        );
      } catch (error) {
        failure = error instanceof Error ? error.message : String(error);
      } finally {
        prototype.add = originalAdd;
      }
      return {
        failure,
        callbackCount,
        outbox: await store.getOutboxForOwner(ownerId),
        draft: await store.getDraft({ ownerId, scaleId: submission.scale_id }),
      };
    },
    { ownerId: PATIENT_A, submission: payload(PATIENT_A, SUBMISSION_A) },
  );

  expect(result.failure).toMatch(/abort/i);
  expect(result.callbackCount).toBe(0);
  expect(result.outbox).toEqual([]);
  expect(result.draft).toMatchObject({ revision: 1, answers: { q1: 0 } });
});
