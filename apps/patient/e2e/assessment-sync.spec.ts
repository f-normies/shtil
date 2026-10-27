import { expect, test } from "@playwright/test";
import {
  DEFINITION_V1,
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

test("sync verifies the online patient and coalesces simultaneous same-tab triggers", async ({
  page,
}) => {
  await openPatientPage(page);
  await seedOneQueuedAnswer(page);

  const result = await page.evaluate(
    async ({ ownerId }) => {
      const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
      const { createAssessmentSyncEngine } = await import("/patient/src/assessments/sync.ts");
      const store = await AssessmentStore.open();
      let sessionChecks = 0;
      let submissions = 0;
      let signalStarted: () => void = () => {};
      let releaseSubmission: () => void = () => {};
      const started = new Promise<void>((resolve) => {
        signalStarted = resolve;
      });
      const held = new Promise<void>((resolve) => {
        releaseSubmission = resolve;
      });
      const engine = createAssessmentSyncEngine(store, {
        getCurrentUser: async () => {
          sessionChecks += 1;
          return { id: ownerId, role: "patient" };
        },
        submitAssessment: async () => {
          submissions += 1;
          signalStarted();
          await held;
          return { status: 201 };
        },
      });

      const first = engine.sync();
      await started;
      const second = engine.sync();
      releaseSubmission();
      const outcomes = await Promise.all([first, second]);
      return {
        sessionChecks,
        submissions,
        statuses: outcomes.map((outcome) => outcome.status),
        queued: await store.getOutboxForOwner(ownerId),
      };
    },
    { ownerId: PATIENT_A },
  );

  expect(result.sessionChecks).toBe(1);
  expect(result.submissions).toBe(1);
  expect(result.queued).toEqual([]);
});

test("401 locks local access without deleting queued answers", async ({ page }) => {
  await openPatientPage(page);
  await seedOneQueuedAnswer(page);

  const result = await page.evaluate(
    async ({ ownerId }) => {
      const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
      const { createAssessmentSyncEngine } = await import("/patient/src/assessments/sync.ts");
      const store = await AssessmentStore.open();
      let submissions = 0;
      const engine = createAssessmentSyncEngine(store, {
        getCurrentUser: async () => null,
        submitAssessment: async () => {
          submissions += 1;
          return { status: 201 };
        },
      });
      const outcome = await engine.sync();
      const pointer = await store.getOwnerPointer();
      await store.rememberOnlinePatient({ id: ownerId, role: "patient" });
      return {
        status: outcome.status,
        submissions,
        pointer,
        retained: await store.getOutboxForOwner(ownerId),
      };
    },
    { ownerId: PATIENT_A },
  );

  expect(result.status).toBe("authentication-required");
  expect(result.submissions).toBe(0);
  expect(result.pointer).toEqual({ id: PATIENT_A, role: "patient", locked: true });
  expect(result.retained).toHaveLength(1);
});

test("a doctor session locks the previous patient without deleting the queued answer", async ({
  page,
}) => {
  await openPatientPage(page);
  await seedOneQueuedAnswer(page);

  const result = await page.evaluate(
    async ({ ownerId }) => {
      const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
      const { createAssessmentSyncEngine } = await import("/patient/src/assessments/sync.ts");
      const store = await AssessmentStore.open();
      const engine = createAssessmentSyncEngine(store, {
        getCurrentUser: async () => ({ id: ownerId, role: "doctor" }),
        submitAssessment: async () => ({ status: 201 }),
      });
      const outcome = await engine.sync();
      const pointer = await store.getOwnerPointer();
      let hidden = false;
      try {
        await store.getOutboxForOwner(ownerId);
      } catch {
        hidden = true;
      }
      await store.rememberOnlinePatient({ id: ownerId, role: "patient" });
      return {
        outcome,
        pointer,
        hidden,
        retained: await store.getOutboxForOwner(ownerId),
      };
    },
    { ownerId: PATIENT_A },
  );

  expect(result.outcome.status).toBe("owner-mismatch");
  expect(result.pointer).toEqual({ id: PATIENT_A, role: "patient", locked: true });
  expect(result.hidden).toBe(true);
  expect(result.retained).toHaveLength(1);
});

test("a patient mismatch response locks the checked owner and retains queued answers", async ({
  page,
}) => {
  await openPatientPage(page);
  await seedOneQueuedAnswer(page);

  const result = await page.evaluate(
    async ({ ownerId }) => {
      const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
      const { createAssessmentSyncEngine } = await import("/patient/src/assessments/sync.ts");
      const store = await AssessmentStore.open();
      const engine = createAssessmentSyncEngine(store, {
        getCurrentUser: async () => ({ id: ownerId, role: "patient" }),
        submitAssessment: async () => ({ status: 403, code: "patient_mismatch" }),
      });
      const outcome = await engine.sync();
      const pointer = await store.getOwnerPointer();
      let hidden = false;
      try {
        await store.getOutboxForOwner(ownerId);
      } catch {
        hidden = true;
      }
      await store.rememberOnlinePatient({ id: ownerId, role: "patient" });
      return {
        outcome,
        pointer,
        hidden,
        retained: await store.getOutboxForOwner(ownerId),
      };
    },
    { ownerId: PATIENT_A },
  );

  expect(result.outcome.status).toBe("owner-mismatch");
  expect(result.pointer).toEqual({ id: PATIENT_A, role: "patient", locked: true });
  expect(result.hidden).toBe(true);
  expect(result.retained).toHaveLength(1);
});

test("a 403 from an old submission does not lock a newer owner selected in another tab", async ({
  page,
}) => {
  await openPatientPage(page);
  await seedOneQueuedAnswer(page);

  const result = await page.evaluate(
    async ({ oldOwnerId, newOwnerId }) => {
      const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
      const { createAssessmentSyncEngine } = await import("/patient/src/assessments/sync.ts");
      const store = await AssessmentStore.open();
      const engine = createAssessmentSyncEngine(store, {
        getCurrentUser: async () => ({ id: oldOwnerId, role: "patient" }),
        submitAssessment: async () => {
          await store.rememberOnlinePatient({ id: newOwnerId, role: "patient" });
          return { status: 403, code: "patient_mismatch" };
        },
      });
      const outcome = await engine.sync();
      const newOwnerPointer = await store.getOwnerPointer();
      let oldOwnerHidden = false;
      try {
        await store.getOutboxForOwner(oldOwnerId);
      } catch {
        oldOwnerHidden = true;
      }
      await store.rememberOnlinePatient({ id: oldOwnerId, role: "patient" });
      return {
        outcome,
        newOwnerPointer,
        oldOwnerHidden,
        retained: await store.getOutboxForOwner(oldOwnerId),
      };
    },
    { oldOwnerId: PATIENT_A, newOwnerId: PATIENT_B },
  );

  expect(result.outcome.status).toBe("owner-mismatch");
  expect(result.newOwnerPointer).toEqual({ id: PATIENT_B, role: "patient", locked: false });
  expect(result.oldOwnerHidden).toBe(true);
  expect(result.retained).toHaveLength(1);
});

test("a permanent error blocks one answer while later answers continue unchanged", async ({
  page,
}) => {
  await openPatientPage(page);
  await seedOneQueuedAnswer(page, PATIENT_A, SUBMISSION_A);
  const saved = await storeCall<{ status: "saved"; draft: { revision: number } }>(
    page,
    "saveDraft",
    {
      ownerId: PATIENT_A,
      scaleId: SCALE_ID,
      expectedRevision: 0,
      answers: { q1: 1 },
      definitionSnapshot: DEFINITION_V1,
    },
  );
  expect(saved.status).toBe("saved");
  const enqueued = await storeCall<{ status: "queued" }>(page, "enqueueDraft", {
    ownerId: PATIENT_A,
    scaleId: SCALE_ID,
    expectedRevision: saved.draft.revision,
    payload: payload(PATIENT_A, SUBMISSION_B, { q1: 1 }),
  });
  expect(enqueued.status).toBe("queued");

  const result = await page.evaluate(
    async ({ ownerId }) => {
      const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
      const { createAssessmentSyncEngine } = await import("/patient/src/assessments/sync.ts");
      const store = await AssessmentStore.open();
      const payloads: unknown[] = [];
      const engine = createAssessmentSyncEngine(store, {
        getCurrentUser: async () => ({ id: ownerId, role: "patient" }),
        submitAssessment: async (body) => {
          payloads.push(body);
          return payloads.length === 1
            ? { status: 404, code: "scale_unavailable" }
            : { status: 201 };
        },
      });
      const outcome = await engine.sync();
      return { outcome, payloads, retained: await store.getOutboxForOwner(ownerId) };
    },
    { ownerId: PATIENT_A },
  );

  expect(result.payloads).toEqual([payload(), payload(PATIENT_A, SUBMISSION_B, { q1: 1 })]);
  expect(result.retained).toHaveLength(1);
  expect(result.retained[0]).toMatchObject({
    submission_id: SUBMISSION_A,
    payload: payload(),
    definition_snapshot: DEFINITION_V1,
    state: "blocked",
    last_error: { status: 404, code: "scale_unavailable" },
  });
  expect(result.outcome).toMatchObject({ submitted: 1, blocked: 1 });
});

test("retry after a server failure sends the original idempotency key and payload", async ({
  page,
}) => {
  await openPatientPage(page);
  await seedOneQueuedAnswer(page);

  const result = await page.evaluate(
    async ({ ownerId }) => {
      const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
      const { createAssessmentSyncEngine } = await import("/patient/src/assessments/sync.ts");
      const store = await AssessmentStore.open();
      const payloads: unknown[] = [];
      let attempts = 0;
      const engine = createAssessmentSyncEngine(store, {
        getCurrentUser: async () => ({ id: ownerId, role: "patient" }),
        submitAssessment: async (body) => {
          payloads.push(body);
          attempts += 1;
          return attempts === 1 ? { status: 503 } : { status: 200 };
        },
      });
      const first = await engine.sync();
      const retainedAfterFailure = await store.getOutboxForOwner(ownerId);
      const second = await engine.sync();
      return {
        first,
        second,
        payloads,
        retainedAfterFailure,
        remaining: await store.getOutboxForOwner(ownerId),
      };
    },
    { ownerId: PATIENT_A },
  );

  expect(result.first.status).toBe("waiting");
  expect(result.retainedAfterFailure).toHaveLength(1);
  expect(result.payloads).toEqual([payload(), payload()]);
  expect(result.second.status).toBe("complete");
  expect(result.remaining).toEqual([]);
});
