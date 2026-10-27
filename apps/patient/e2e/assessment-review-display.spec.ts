import type { Schemas } from "@compass/api-client";
import { expect, type Page, test } from "@playwright/test";
import { mockAssessmentApi } from "./assessment-api-mock";
import { DEFINITION_FORM, PATIENT_A, SCALE_ID, SUBMISSION_B } from "./assessment-fixtures";
import { storeCall } from "./assessment-test-helpers";

const SYNTHETIC_SOURCE = {
  id: "synthetic-review-source",
  title: "Synthetic review source",
  url: "https://example.invalid/review",
  supplementary: true,
} satisfies Schemas["SourceOut"];

const SYNTHETIC_SCORE_SOURCE = {
  ...SYNTHETIC_SOURCE,
  locator: "section 1",
} satisfies Schemas["ScoreBandSourceOut"];

test("review regression: unfiltered charts keep equal score names from different scales separate", async ({
  page,
}) => {
  const api = await mockAssessmentApi(page.context(), {
    history: [
      assessmentResult("00000000-0000-4000-8000-0000000000d1", "synthetic-scale-a", "Scale A"),
      assessmentResult("00000000-0000-4000-8000-0000000000d2", "synthetic-scale-b", "Scale B"),
    ],
  });
  api.setCatalogFailure(503);
  await page.goto("/patient/");
  await expect(page.getByText("Показана история всех ответов.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Загрузить ещё" })).toBeVisible();
  await page.getByRole("button", { name: "Загрузить ещё" }).click();

  await expect(page.locator("figure")).toHaveCount(2);
  await expect(page.getByText(/Scale A/).first()).toBeVisible();
  await expect(page.getByText(/Scale B/).first()).toBeVisible();
});

test("review regression: an outbox read error is visible and can be retried", async ({ page }) => {
  const api = await mockAssessmentApi(page.context());
  await page.goto("/patient/");
  await expect(page.getByRole("link", { name: "Заполнить" })).toBeVisible();
  api.setNetworkAvailable(false);
  await seedQueuedSubmission(page, SUBMISSION_B);

  await page.getByRole("link", { name: "Заполнить" }).click();
  await expect(page.getByRole("heading", { name: DEFINITION_FORM.scale.title })).toBeVisible();
  await page.evaluate(() => {
    const prototype = IDBIndex.prototype;
    const nativeGetAll = prototype.getAll;
    let shouldFail = true;
    prototype.getAll = function (...args) {
      if (shouldFail && window.location.pathname === "/patient/" && this.name === "owner_created") {
        shouldFail = false;
        prototype.getAll = nativeGetAll;
        throw new DOMException("Synthetic outbox read failure", "UnknownError");
      }
      return Reflect.apply(nativeGetAll, this, args);
    };
  });
  await page.getByRole("link", { name: "Назад" }).click();

  await expect(page.getByRole("alert")).toContainText("Не удалось прочитать сохранённые ответы");
  const retry = page.getByRole("button", { name: "Повторить чтение" });
  await expect(retry).toBeVisible();
  await retry.click();
  await expect(page.getByRole("heading", { name: "Ожидают отправки" })).toBeVisible();
  await expect(page.getByText("I felt calm. — Sometimes")).toBeVisible();
});

async function seedQueuedSubmission(page: Page, submissionId: string): Promise<void> {
  await storeCall(page, "rememberOnlinePatient", { id: PATIENT_A, role: "patient" });
  await storeCall(page, "cacheDefinition", DEFINITION_FORM);
  const saved = await storeCall<{ draft: { revision: number } }>(page, "saveDraft", {
    ownerId: PATIENT_A,
    scaleId: SCALE_ID,
    expectedRevision: 0,
    answers: { calm: 2, rest: 4 },
    definitionSnapshot: DEFINITION_FORM,
  });
  await storeCall(page, "enqueueDraft", {
    ownerId: PATIENT_A,
    scaleId: SCALE_ID,
    expectedRevision: saved.draft.revision,
    payload: {
      patient_id: PATIENT_A,
      submission_id: submissionId,
      scale_id: SCALE_ID,
      scale_version: DEFINITION_FORM.scale.version,
      completed_at: "2026-10-17T10:00:00.000Z",
      answers: { calm: 2, rest: 4 },
    },
  });
}

function assessmentResult(id: string, scaleId: string, title: string): Schemas["AssessmentOut"] {
  return {
    id,
    patient_id: PATIENT_A,
    submission_id: id,
    scale_id: scaleId,
    scale_title: title,
    scale_version: 1,
    completed_at: "2026-10-16T10:00:00.000Z",
    received_at: "2026-10-16T10:01:00.000Z",
    answers: { shared: 2 },
    scores: {
      shared: {
        value: 2,
        band: {
          code: "synthetic",
          label: "Synthetic band",
          source: SYNTHETIC_SCORE_SOURCE,
        },
      },
    },
    sources: [SYNTHETIC_SOURCE],
  };
}
