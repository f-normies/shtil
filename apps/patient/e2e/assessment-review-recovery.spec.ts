import type { Schemas } from "@compass/api-client";
import { expect, type Page, test } from "@playwright/test";
import { mockAssessmentApi } from "./assessment-api-mock";
import { DEFINITION_FORM, PATIENT_A, payload, SCALE_ID, SUBMISSION_B } from "./assessment-fixtures";
import { storeCall } from "./assessment-test-helpers";

test("review regression: a failed 401 lock write denies offline access and keeps the draft", async ({
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
  await page.evaluate(async () => {
    const { getAssessmentStore } = await import("/patient/src/session.ts");
    const store = await getAssessmentStore();
    store.lockOwnerPointer = async () => {
      throw new DOMException("Synthetic IndexedDB lock failure", "UnknownError");
    };
  });

  api.setNextLogoutStatus(401);
  await page.getByRole("button", { name: "Выйти" }).click();
  const dialog = page.getByRole("alertdialog", { name: "Выход с локальными ответами" });
  await dialog.getByRole("button", { name: "Удалить локальные ответы и выйти" }).click();
  await expect(page.getByLabel("Электронная почта", { exact: true })).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("Не удалось сохранить блокировку доступа");

  api.setNetworkAvailable(false);
  await page.goto("/patient/");
  await expect(
    page.getByText("Для первого входа и загрузки опросников нужно подключение к интернету."),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "Ожидают отправки" })).toHaveCount(0);
  expect(
    await storeCall<{ drafts: number; outbox: number }>(page, "getOwnerDataCounts", PATIENT_A),
  ).toEqual({ drafts: 1, outbox: 0, audio: 0 });
  expect(
    await storeCall<Record<string, unknown> | null>(page, "getDraft", {
      ownerId: PATIENT_A,
      scaleId: SCALE_ID,
    }),
  ).toMatchObject({ answers: { calm: 2 }, definition_snapshot: { scale: { version: 1 } } });
});

test("review regression: discarding an older-version draft requires consent and a fresh revision", async ({
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
  await expect(page.getByRole("heading", { name: "Сохранённый черновик версии 1" })).toBeVisible();
  await expect(
    page.getByRole("listitem").filter({ hasText: "I felt calm.: Sometimes" }),
  ).toHaveText("I felt calm.: Sometimes");
  await page.getByRole("button", { name: "Начать версию 2 без старого черновика" }).click();

  const dialog = page.getByRole("alertdialog", { name: "Удалить черновик версии 1?" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Оставить старый черновик" }).click();
  expect(await readStoredDrafts(page)).toContainEqual(
    expect.objectContaining({
      owner_id: PATIENT_A,
      scale_id: SCALE_ID,
      revision: 1,
      answers: { calm: 2 },
      definition_snapshot: expect.objectContaining({
        scale: expect.objectContaining({ version: 1 }),
      }),
    }),
  );

  await page.getByRole("button", { name: "Начать версию 2 без старого черновика" }).click();
  const concurrentSave = await storeCall<{ status: string }>(page, "saveDraft", {
    ownerId: PATIENT_A,
    scaleId: SCALE_ID,
    expectedRevision: 1,
    answers: { calm: 5 },
    definitionSnapshot: DEFINITION_FORM,
  });
  expect(concurrentSave.status).toBe("saved");
  await dialog.getByRole("button", { name: "Удалить и начать версию 2" }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "Черновик изменился в другой вкладке" }),
  ).toBeVisible();
  expect(await readStoredDrafts(page)).toContainEqual(
    expect.objectContaining({ revision: 2, answers: { calm: 5 } }),
  );

  await page.getByRole("button", { name: "Начать версию 2 без старого черновика" }).click();
  await page
    .getByRole("alertdialog", { name: "Удалить черновик версии 1?" })
    .getByRole("button", { name: "Удалить и начать версию 2" })
    .click();
  const newQuestion = page.getByRole("group", { name: "I felt calm. (version 2)" });
  await expect(newQuestion).toBeVisible();
  await expect(newQuestion.getByRole("radio", { name: "Often (version 2)" })).not.toBeChecked();
  expect(await readStoredDrafts(page)).toEqual([]);
});

test("an enqueue in another tab clears stale old-version recovery without deleting the outbox", async ({
  page,
  context,
}) => {
  const api = await mockAssessmentApi(context);
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
  await expect(page.getByRole("heading", { name: "Сохранённый черновик версии 1" })).toBeVisible();
  await page.getByRole("button", { name: "Начать версию 2 без старого черновика" }).click();
  const dialog = page.getByRole("alertdialog", { name: "Удалить черновик версии 1?" });
  await expect(dialog).toBeVisible();

  const secondTab = await context.newPage();
  try {
    await secondTab.goto("/patient/login");
    await expect(secondTab.getByRole("heading", { name: /Синтетический пациент А/ })).toBeVisible();
    api.setNetworkAvailable(false);
    const queued = await storeCall<{ status: string }>(secondTab, "enqueueDraft", {
      ownerId: PATIENT_A,
      scaleId: SCALE_ID,
      expectedRevision: 1,
      payload: payload(PATIENT_A, SUBMISSION_B, { calm: 2 }),
    });
    expect(queued.status).toBe("queued");

    await dialog.getByRole("button", { name: "Удалить и начать версию 2" }).click();
    await expect(page.getByRole("heading", { name: "Сохранённый черновик версии 1" })).toHaveCount(
      0,
    );
    const updatedCalmQuestion = page.getByRole("group", { name: "I felt calm. (version 2)" });
    await expect(
      updatedCalmQuestion.getByRole("radio", { name: "Sometimes (version 2)" }),
    ).toBeEnabled();
    await expect(
      updatedCalmQuestion.getByRole("radio", { name: "Sometimes (version 2)" }),
    ).not.toBeChecked();
    expect(
      await storeCall<Record<string, unknown>[]>(page, "getOutboxForOwner", PATIENT_A),
    ).toMatchObject([
      {
        submission_id: SUBMISSION_B,
        payload: { patient_id: PATIENT_A, scale_version: 1, answers: { calm: 2 } },
        definition_snapshot: { scale: { version: 1 } },
      },
    ]);
  } finally {
    await secondTab.close();
  }
});

test("a lock denial in one tab immediately closes another tab without deleting answers", async ({
  page,
  context,
}) => {
  const api = await mockAssessmentApi(context);
  await page.goto("/patient/");
  await page.getByRole("link", { name: "Заполнить" }).click();
  await page
    .getByRole("group", { name: "I felt calm." })
    .getByRole("radio", { name: "Sometimes" })
    .check();
  await expect(page.getByText("Ответы сохранены на устройстве.")).toBeVisible();

  const secondTab = await context.newPage();
  try {
    await secondTab.goto("/patient/");
    await expect(secondTab.getByRole("heading", { name: /Синтетический пациент А/ })).toBeVisible();
    await failOwnerLockWrite(page);
    await failOwnerLockWrite(secondTab);
    api.setNextLogoutStatus(401);
    await secondTab.getByRole("button", { name: "Выйти" }).click();
    await secondTab
      .getByRole("alertdialog", { name: "Выход с локальными ответами" })
      .getByRole("button", { name: "Удалить локальные ответы и выйти" })
      .click();
    await expect(secondTab.getByLabel("Электронная почта", { exact: true })).toBeVisible();

    await expect(page.getByLabel("Электронная почта", { exact: true })).toBeVisible();
    await expect(page.getByRole("group", { name: "I felt calm." })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Заполнить" })).toHaveCount(0);
    expect(await storeCall(page, "getOwnerPointer")).toMatchObject({
      id: PATIENT_A,
      locked: false,
    });
    expect(
      await storeCall<Record<string, unknown>[]>(page, "getOutboxForOwner", PATIENT_A),
    ).toEqual([]);
    expect(
      await storeCall<Record<string, unknown> | null>(page, "getDraft", {
        ownerId: PATIENT_A,
        scaleId: SCALE_ID,
      }),
    ).toMatchObject({ answers: { calm: 2 }, definition_snapshot: { scale: { version: 1 } } });
  } finally {
    await secondTab.close();
  }
});

async function failOwnerLockWrite(page: Page): Promise<void> {
  await page.evaluate(async () => {
    const { getAssessmentStore } = await import("/patient/src/session.ts");
    const store = await getAssessmentStore();
    store.lockOwnerPointer = async () => {
      throw new DOMException("Synthetic IndexedDB lock failure", "UnknownError");
    };
  });
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
