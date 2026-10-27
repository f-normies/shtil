import type { Schemas } from "@compass/api-client";
import { expect, type Page, test } from "@playwright/test";
import { mockAssessmentApi, SYNTHETIC_USERS } from "./assessment-api-mock";
import {
  DEFINITION_FORM,
  PATIENT_A,
  PATIENT_B,
  SCALE_ID,
  SUBMISSION_A,
} from "./assessment-fixtures";
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

async function login(page: Page, email: string): Promise<void> {
  await page.getByLabel("Электронная почта", { exact: true }).fill(email);
  await page.getByLabel("Пароль", { exact: true }).fill("synthetic-password");
  await page.getByRole("button", { name: "Войти" }).click();
}

async function logoutAndConfirmIfNeeded(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Выйти" }).click();
  const dialog = page.getByRole("alertdialog", { name: "Выход с локальными ответами" });
  await expect
    .poll(
      async () =>
        (await dialog.isVisible().catch(() => false)) ||
        (await page
          .getByLabel("Электронная почта", { exact: true })
          .isVisible()
          .catch(() => false)),
    )
    .toBe(true);
  if (await dialog.isVisible().catch(() => false)) {
    await dialog.getByRole("button", { name: "Удалить локальные ответы и выйти" }).click();
  }
  await expect(page.getByLabel("Электронная почта", { exact: true })).toBeVisible();
}

async function readStoredDrafts(page: Page): Promise<Record<string, unknown>[]> {
  return page.evaluate(async () => {
    const { openAssessmentDatabase } = await import("/patient/src/assessments/database.ts");
    const database = await openAssessmentDatabase();
    return new Promise<Record<string, unknown>[]>((resolve, reject) => {
      const transaction = database.transaction("drafts", "readonly");
      const request = transaction.objectStore("drafts").getAll();
      request.addEventListener("success", () => resolve(request.result));
      request.addEventListener("error", () => reject(request.error));
    });
  });
}

test("review regression: a shared-account switch revalidates route ownership before showing history", async ({
  page,
  context,
}) => {
  const history = [
    assessmentResult("00000000-0000-4000-8000-0000000000c1", PATIENT_A, "History result for A"),
    assessmentResult("00000000-0000-4000-8000-0000000000c2", PATIENT_B, "History result for B"),
  ];
  await mockAssessmentApi(context, { history });
  const secondTab = await context.newPage();

  try {
    await page.goto("/patient/");
    await expect(page.getByRole("heading", { name: /Синтетический пациент А/ })).toBeVisible();
    await expect(page.getByText("History result for A", { exact: true })).toBeVisible();
    await page.getByRole("link", { name: "Заполнить" }).click();
    const oldQuestion = page.getByRole("group", { name: "I felt calm." });
    await oldQuestion.getByRole("radio", { name: "Sometimes" }).check();
    await expect(page.getByText("Ответы сохранены на устройстве.")).toBeVisible();

    await secondTab.goto("/patient/");
    await expect(secondTab.getByRole("heading", { name: /Синтетический пациент А/ })).toBeVisible();
    await logoutAndConfirmIfNeeded(secondTab);
    await login(secondTab, SYNTHETIC_USERS.b.email);
    await expect(secondTab.getByRole("heading", { name: /Синтетический пациент Б/ })).toBeVisible();
    await expect(secondTab.getByText("History result for B", { exact: true })).toBeVisible();

    await expect
      .poll(async () => (await storeCall<{ id: string } | null>(page, "getOwnerPointer"))?.id)
      .toBe(PATIENT_B);
    const oldAnswerIsChecked = await oldQuestion
      .getByRole("radio", { name: "Sometimes" })
      .evaluateAll((elements) =>
        elements.some((element) => element instanceof HTMLInputElement && element.checked),
      );
    expect.soft(oldAnswerIsChecked).toBe(false);
    const backLink = page.getByRole("link", { name: "Назад" });
    if (await backLink.isVisible().catch(() => false)) await backLink.click();
    await expect
      .poll(
        async () => {
          const loginVisible = await page
            .getByLabel("Электронная почта", { exact: true })
            .isVisible()
            .catch(() => false);
          const historyB = await page
            .getByText("History result for B", { exact: true })
            .isVisible()
            .catch(() => false);
          return loginVisible || historyB;
        },
        { timeout: 5_000 },
      )
      .toBe(true);
    const loginVisible = await page
      .getByLabel("Электронная почта", { exact: true })
      .isVisible()
      .catch(() => false);
    if (!loginVisible) {
      await expect(page.getByRole("heading", { name: /Синтетический пациент А/ })).toHaveCount(0);
      await expect(page.getByRole("heading", { name: /Синтетический пациент Б/ })).toBeVisible();
    }
  } finally {
    await secondTab.close();
  }
});

test("review regression: a delayed account response cannot overwrite a newer tab login", async ({
  page,
  context,
}) => {
  const api = await mockAssessmentApi(context);
  const secondTab = await context.newPage();
  try {
    await page.goto("/patient/");
    await page.getByRole("link", { name: "Заполнить" }).click();
    await page
      .getByRole("group", { name: "I felt calm." })
      .getByRole("radio", { name: "Sometimes" })
      .check();
    await expect(page.getByText("Ответы сохранены на устройстве.")).toBeVisible();

    api.holdNextSessionResponse(page);
    await secondTab.goto("/patient/");
    await logoutAndConfirmIfNeeded(secondTab);
    await login(secondTab, SYNTHETIC_USERS.b.email);
    await expect(secondTab.getByRole("heading", { name: /Синтетический пациент Б/ })).toBeVisible();
    await expect.poll(() => api.heldSessionResponses()).toBe(1);
    const oldAnswerIsChecked = await page
      .getByRole("group", { name: "I felt calm." })
      .getByRole("radio", { name: "Sometimes" })
      .evaluateAll((elements) =>
        elements.some((element) => element instanceof HTMLInputElement && element.checked),
      );
    expect.soft(oldAnswerIsChecked).toBe(false);
    await logoutAndConfirmIfNeeded(secondTab);
    await login(secondTab, SYNTHETIC_USERS.a.email);
    await expect(secondTab.getByRole("heading", { name: /Синтетический пациент А/ })).toBeVisible();
    api.releaseHeldSessionResponse();

    await expect
      .poll(() => storeCall<{ id: string; locked: boolean } | null>(page, "getOwnerPointer"))
      .toMatchObject({ id: PATIENT_A, locked: false });
    const backLink = page.getByRole("link", { name: "Назад" });
    if (await backLink.isVisible().catch(() => false)) await backLink.click();
    await expect(page.getByRole("heading", { name: /Синтетический пациент А/ })).toBeVisible();
    await expect(page.getByRole("link", { name: "Заполнить" })).toBeVisible();
    expect(api.submissions).toHaveLength(0);
  } finally {
    api.releaseHeldSessionResponse();
    await secondTab.close();
  }
});

test("review regression: a logout 401 locks offline access but preserves the draft", async ({
  page,
}) => {
  const api = await mockAssessmentApi(page.context());
  await page.goto("/patient/");
  await page.getByRole("link", { name: "Заполнить" }).click();
  await page
    .getByRole("group", { name: "I felt calm." })
    .getByRole("radio", { name: "Sometimes" })
    .check();
  await expect(page.getByText("Ответы сохранены на устройстве.")).toBeVisible();
  await page.getByRole("link", { name: "Назад" }).click();

  api.setNextLogoutStatus(401);
  await page.getByRole("button", { name: "Выйти" }).click();
  const dialog = page.getByRole("alertdialog", { name: "Выход с локальными ответами" });
  await dialog.getByRole("button", { name: "Удалить локальные ответы и выйти" }).click();
  await expect(page.getByLabel("Электронная почта", { exact: true })).toBeVisible();
  await expect
    .poll(() => storeCall<{ id: string; locked: boolean } | null>(page, "getOwnerPointer"))
    .toMatchObject({ id: PATIENT_A, locked: true });

  api.setNetworkAvailable(false);
  await page.goto("/patient/");
  await expect(
    page.getByText("Для первого входа и загрузки опросников нужно подключение к интернету."),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "Ожидают отправки" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Заполнить" })).toHaveCount(0);

  api.setNetworkAvailable(true);
  api.setAuthenticatedUser(SYNTHETIC_USERS.a);
  await page.reload();
  await page.getByRole("link", { name: "Заполнить" }).click();
  await expect(
    page.getByRole("group", { name: "I felt calm." }).getByRole("radio", { name: "Sometimes" }),
  ).toBeChecked();
});

test("review regression: a version-two form never reuses or overwrites a version-one draft", async ({
  page,
}) => {
  const api = await mockAssessmentApi(page.context());
  await page.goto("/patient/");
  await page.getByRole("link", { name: "Заполнить" }).click();
  await page
    .getByRole("group", { name: "I felt calm." })
    .getByRole("radio", { name: "Sometimes" })
    .check();
  await expect(page.getByText("Ответы сохранены на устройстве.")).toBeVisible();
  await page.getByRole("link", { name: "Назад" }).click();

  api.setDefinitions([versionTwoDefinition()]);
  await page.reload();
  await page.getByRole("link", { name: "Заполнить" }).click();
  const newQuestion = page.getByRole("group", { name: "I felt calm. (version 2)" });
  await expect
    .soft(newQuestion.getByRole("radio", { name: "Sometimes (version 2)" }))
    .not.toBeChecked();

  const answer = newQuestion.getByRole("radio", { name: "Often (version 2)" });
  if (await answer.isEnabled()) {
    await answer.check();
    await expect(page.getByText("Ответы сохранены на устройстве.")).toBeVisible();
  }
  const drafts = await readStoredDrafts(page);
  expect(drafts).toContainEqual(
    expect.objectContaining({
      owner_id: PATIENT_A,
      scale_id: SCALE_ID,
      answers: { calm: 2 },
      definition_snapshot: expect.objectContaining({
        scale: expect.objectContaining({ version: 1 }),
      }),
    }),
  );
});

test("review regression: only the current submission's ACK confirms its result", async ({
  page,
}) => {
  const api = await mockAssessmentApi(page.context());
  await page.goto("/patient/login");
  api.holdNextSubmissionResponse();
  await seedQueuedSubmission(page, SUBMISSION_A);
  await page.goto("/patient/");
  await expect(page.getByRole("heading", { name: /Синтетический пациент А/ })).toBeVisible();
  await expect.poll(() => api.heldSubmissionResponses()).toBe(1);
  await page.getByRole("link", { name: "Заполнить" }).click();

  try {
    await page
      .getByRole("group", { name: "I felt calm." })
      .getByRole("radio", { name: "Sometimes" })
      .check();
    await page
      .getByRole("group", { name: "I could rest." })
      .getByRole("radio", { name: "Often" })
      .check();
    await page.getByRole("button", { name: "Отправить ответы" }).click();
    await expect
      .poll(() => storeCall<unknown[]>(page, "getOutboxForOwner", PATIENT_A))
      .toHaveLength(2);
    expect(api.submissions.map((item) => item.submission_id)).toEqual([SUBMISSION_A]);

    api.releaseHeldSubmissionResponse();
    await expect.poll(() => api.acknowledgedSubmissions.length).toBe(1);
    expect(api.acknowledgedSubmissions.map((item) => item.submission_id)).toEqual([SUBMISSION_A]);
    await expect(page.getByText("Результат сохранён на сервере.")).toHaveCount(0);
    await expect
      .poll(() => storeCall<unknown[]>(page, "getOutboxForOwner", PATIENT_A))
      .toHaveLength(1);
  } finally {
    api.releaseHeldSubmissionResponse();
  }
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

function versionTwoDefinition(): Schemas["ScaleDefinitionOut"] {
  return {
    ...DEFINITION_FORM,
    scale: {
      ...DEFINITION_FORM.scale,
      version: 2,
      items: DEFINITION_FORM.scale.items.map((item) => ({
        ...item,
        text: `${item.text} (version 2)`,
        options: item.options.map((option) => ({ ...option, text: `${option.text} (version 2)` })),
      })),
    },
  };
}

function assessmentResult(id: string, patientId: string, title: string): Schemas["AssessmentOut"] {
  return {
    id,
    patient_id: patientId,
    submission_id: id,
    scale_id: SCALE_ID,
    scale_title: title,
    scale_version: 1,
    completed_at: "2026-10-16T10:00:00.000Z",
    received_at: "2026-10-16T10:01:00.000Z",
    answers: { calm: 2 },
    scores: {
      calmness: {
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
