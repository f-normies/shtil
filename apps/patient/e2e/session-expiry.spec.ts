import type { Page } from "@playwright/test";
import { expect, test } from "@playwright/test";
import { PATIENT_A, PATIENT_B, SUBMISSION_A } from "./assessment-fixtures";
import { seedOneQueuedAnswer, storeCall } from "./assessment-test-helpers";
import { mockSessionApi } from "./session-api-mock";

/** Count queued answers directly in IndexedDB: the store API refuses a locked owner. */
function countQueuedAnswers(page: Page, ownerId: string): Promise<number> {
  return page.evaluate(async (owner) => {
    const database = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open("clin-compass-assessments");
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    const transaction = database.transaction("outbox", "readonly");
    const request = transaction
      .objectStore("outbox")
      .index("owner_id")
      .count(IDBKeyRange.only(owner));
    const count = await new Promise<number>((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    database.close();
    return count;
  }, ownerId);
}

async function failLockWrites(page: Page): Promise<void> {
  await page.evaluate(async () => {
    const { getAssessmentStore } = await import("/patient/src/session.ts");
    const store = await getAssessmentStore();
    store.lockOwnerPointer = async () => {
      throw new DOMException("Synthetic IndexedDB lock failure", "UnknownError");
    };
  });
}

test("a 401 while loading a patient form locks local access and keeps the queued answer", async ({
  page,
}) => {
  const api = await mockSessionApi(page.context(), "patient");
  const cancelledErrors: string[] = [];
  page.on("pageerror", (error) => {
    if (error.name === "CancelledError") cancelledErrors.push(error.stack ?? error.message);
  });
  page.on("console", (message) => {
    if (message.type() === "error" && message.text().includes("CancelledError")) {
      cancelledErrors.push(message.text());
    }
  });
  await page.goto("/patient/");
  await expect(page.getByRole("heading", { name: /Здравствуйте/ })).toBeVisible();
  api.setAssessmentSubmissionNetworkAvailable(false);
  await seedOneQueuedAnswer(page);
  api.expireOnDefinition();
  await page.getByRole("link", { name: "Заполнить" }).click();

  await expect(page.getByLabel("Электронная почта", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Здравствуйте/ })).toHaveCount(0);
  await expect(
    page.getByText("Не удалось открыть опросник. Проверьте связь и попробуйте ещё раз."),
  ).toHaveCount(0);
  expect(await storeCall(page, "getOwnerPointer")).toEqual({
    id: PATIENT_A,
    role: "patient",
    locked: true,
  });
  expect(await countQueuedAnswers(page, PATIENT_A)).toBe(1);
  expect(api.assessmentSubmissionResponses).toContainEqual({
    submissionId: SUBMISSION_A,
    status: null,
  });
  expect(api.assessmentSubmissionResponses).not.toContainEqual({
    submissionId: SUBMISSION_A,
    status: 201,
  });
  expect(cancelledErrors).toEqual([]);
  expect(
    api.calls.filter(
      ({ method, path }) =>
        method === "GET" && path === "/api/v1/assessments/scales/synthetic-scale",
    ),
  ).toHaveLength(1);
});

test("a failed lock write after a 401 on a patient form denies offline access", async ({
  page,
}) => {
  const api = await mockSessionApi(page.context(), "patient");
  await page.goto("/patient/");
  await expect(page.getByRole("heading", { name: /Здравствуйте/ })).toBeVisible();
  api.setAssessmentSubmissionNetworkAvailable(false);
  await seedOneQueuedAnswer(page);
  await failLockWrites(page);
  api.expireOnDefinition();
  await page.getByRole("link", { name: "Заполнить" }).click();

  await expect(page.getByLabel("Электронная почта", { exact: true })).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("Не удалось сохранить блокировку доступа");
  expect(await countQueuedAnswers(page, PATIENT_A)).toBe(1);
  expect(api.assessmentSubmissionResponses).toContainEqual({
    submissionId: SUBMISSION_A,
    status: null,
  });
  expect(api.assessmentSubmissionResponses).not.toContainEqual({
    submissionId: SUBMISSION_A,
    status: 201,
  });
});

for (const nextPatientId of [PATIENT_A, PATIENT_B]) {
  test(`an old session check cannot close a new patient session (${nextPatientId === PATIENT_A ? "same patient" : "different patient"})`, async ({
    page,
  }) => {
    const api = await mockSessionApi(page.context(), "patient", { holdSecondSessionCheck: true });
    api.setLoginRole("patient");
    await page.goto("/patient/");
    await expect(page.getByRole("heading", { name: /Здравствуйте/ })).toBeVisible();
    await api.waitForHeldSessionCheck();

    api.expireOnDefinition();
    await page.getByRole("link", { name: "Заполнить" }).click();
    await expect(page.getByLabel("Электронная почта", { exact: true })).toBeVisible();
    api.setLoginPatientId(nextPatientId);
    await page.getByLabel("Электронная почта", { exact: true }).fill("patient@example.invalid");
    await page.getByLabel("Пароль", { exact: true }).fill("synthetic-password");
    await page.getByRole("button", { name: "Войти" }).click();
    await expect(page).toHaveURL(/\/patient\/$/);
    await expect(page.getByRole("heading", { name: /Здравствуйте/ })).toBeVisible();

    api.releaseHeldSessionCheck();
    await expect(page).toHaveURL(/\/patient\/$/);
    await expect(page.getByRole("heading", { name: /Здравствуйте/ })).toBeVisible();
    await expect
      .poll(() => storeCall(page, "getOwnerPointer"))
      .toEqual({
        id: nextPatientId,
        role: "patient",
        locked: false,
      });
  });
}

test("a patient-data 401 closes the doctor cabinet without retry", async ({ page }) => {
  const api = await mockSessionApi(page.context(), "doctor");
  api.rejectPatientList();
  await page.goto("/doctor/");

  await expect(page).toHaveURL(/\/doctor\/login$/);
  await expect(page.getByLabel("Электронная почта", { exact: true })).toBeVisible();
  await expect(page.getByText("не удалось загрузить список пациентов.")).toHaveCount(0);
  expect(
    api.calls.filter(({ method, path }) => method === "GET" && path === "/api/v1/patients"),
  ).toHaveLength(1);
});

test("a WebSocket session end replaces the doctor page with login", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  const api = await mockSessionApi(page.context(), null);
  api.setLoginRole("doctor");
  await page.goto("/doctor/login");
  const historyLength = await page.evaluate(() => history.length);
  const socketSessionEnded = api.endSessionOnSocket();
  await page.getByLabel("Электронная почта", { exact: true }).fill("doctor@example.invalid");
  await page.getByLabel("Пароль", { exact: true }).fill("synthetic-password");
  await page.getByRole("button", { name: "Войти" }).click();
  await socketSessionEnded;

  await expect(page).toHaveURL(/\/doctor\/login$/);
  await expect(page.getByLabel("Электронная почта", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Мои пациенты" })).toHaveCount(0);
  expect(await page.evaluate(() => history.length)).toBe(historyLength);
});

test("a rejected login stays on the form and reports the password error", async ({ page }) => {
  const api = await mockSessionApi(page.context(), null);
  api.setLoginRole("admin");
  await page.goto("/doctor/login");
  await page.getByLabel("Электронная почта", { exact: true }).fill("wrong@example.invalid");
  await page.getByLabel("Пароль", { exact: true }).fill("invalid-password");
  await page.getByRole("button", { name: "Войти" }).click();

  await expect(page).toHaveURL(/\/doctor\/login$/);
  await expect(page.getByRole("alert")).toContainText("неверный email или пароль");
});
