import type { BrowserContext, Page } from "@playwright/test";
import type { AssessmentStore } from "../src/assessments/store";
import { DEFINITION_V1, PATIENT_A, payload, SCALE_ID, SUBMISSION_A } from "./assessment-fixtures";

export async function mockAssessmentAuth(context: BrowserContext): Promise<void> {
  await context.route("**/api/**", (route) =>
    route.fulfill({
      status: 401,
      contentType: "application/problem+json",
      body: JSON.stringify({
        type: "about:blank",
        title: "Unauthenticated test session",
        status: 401,
      }),
    }),
  );
}

export async function openPatientPage(page: Page): Promise<void> {
  // Storage and sync tests seed an authenticated owner directly. Stay on the
  // login route so the home page's /auth/me check cannot lock that test owner.
  await page.goto("/patient/login");
}

export async function storeCall<T>(
  page: Page,
  method: keyof AssessmentStore,
  ...args: unknown[]
): Promise<T> {
  return page.evaluate(
    async ({ methodName, methodArgs }) => {
      type TestWindow = Window & { __assessmentTestStore?: AssessmentStore };
      const testWindow = window as TestWindow;
      if (!testWindow.__assessmentTestStore) {
        const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
        testWindow.__assessmentTestStore = await AssessmentStore.open();
      }
      const target = testWindow.__assessmentTestStore;
      if (!target) throw new Error("Assessment test store did not initialize");
      const operation = target[methodName];
      if (typeof operation !== "function") {
        throw new Error(`Unknown store operation: ${methodName}`);
      }
      return Reflect.apply(operation, target, methodArgs) as Promise<unknown>;
    },
    { methodName: method, methodArgs: args },
  ) as Promise<T>;
}

export async function seedOneQueuedAnswer(
  page: Page,
  ownerId = PATIENT_A,
  submissionId = SUBMISSION_A,
): Promise<unknown> {
  await storeCall(page, "rememberOnlinePatient", { id: ownerId, role: "patient" });
  await storeCall(page, "cacheDefinition", DEFINITION_V1);
  await storeCall(page, "saveDraft", {
    ownerId,
    scaleId: SCALE_ID,
    expectedRevision: 0,
    answers: { q1: 0 },
    definitionSnapshot: DEFINITION_V1,
  });
  return storeCall(page, "enqueueDraft", {
    ownerId,
    scaleId: SCALE_ID,
    expectedRevision: 1,
    payload: payload(ownerId, submissionId),
  });
}
