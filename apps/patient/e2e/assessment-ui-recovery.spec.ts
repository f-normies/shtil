import { expect, test } from "@playwright/test";
import { mockAssessmentApi } from "./assessment-api-mock";
import { DEFINITION_FORM, PATIENT_A, SCALE_ID } from "./assessment-fixtures";

test("a 422 answer can return to its original form for explicit correction", async ({ page }) => {
  const api = await mockAssessmentApi(page.context());
  await page.goto(`/patient/assessments/${SCALE_ID}`);
  await page
    .getByRole("group", { name: "I felt calm." })
    .getByRole("radio", { name: "Sometimes" })
    .check();
  await page
    .getByRole("group", { name: "I could rest." })
    .getByRole("radio", { name: "Often" })
    .check();
  api.setNextSubmissionResponse(422, "invalid_answers");
  await page.getByRole("button", { name: "Отправить ответы" }).click();
  await expect(
    page.getByText("Сохранено на устройстве. Отправим, когда появится связь."),
  ).toBeVisible();

  await page.getByRole("link", { name: "Назад" }).click();
  await expect(page.getByRole("heading", { name: "Нужно проверить" })).toBeVisible();
  await expect(page.getByText(/сервер отклонил ответ/)).toBeVisible();
  await page.getByRole("button", { name: "Исправить ответы" }).click();

  await expect(page).toHaveURL(new RegExp(`/patient/assessments/${SCALE_ID}$`));
  await expect(
    page.getByRole("heading", { name: DEFINITION_FORM.scale.title, level: 1 }),
  ).toBeVisible();
  await expect(
    page.getByRole("group", { name: "I felt calm." }).getByRole("radio", { name: "Sometimes" }),
  ).toBeChecked();
  await expect(
    page.getByRole("group", { name: "I could rest." }).getByRole("radio", { name: "Often" }),
  ).toBeChecked();
  await expect(page.getByRole("heading", { name: "Нужно проверить" })).toHaveCount(0);

  await page
    .getByRole("group", { name: "I felt calm." })
    .getByRole("radio", { name: "Never" })
    .check();
  await expect(page.getByText("Ответы сохранены на устройстве.")).toBeVisible();
  await page.getByRole("button", { name: "Отправить ответы" }).click();
  await expect(page.getByText("Результат сохранён на сервере.")).toBeVisible();
  expect(api.submissions).toHaveLength(2);
  expect(api.acknowledgedSubmissions).toHaveLength(1);
  expect(api.submissions[1]?.submission_id).not.toBe(api.submissions[0]?.submission_id);
  expect(api.acknowledgedSubmissions[0]?.answers).toEqual({ calm: 0, rest: 4 });
});

test("a stale form tab keeps the newer answers saved by another tab", async ({ page }) => {
  const api = await mockAssessmentApi(page.context());
  await page.goto(`/patient/assessments/${SCALE_ID}`);
  const firstTabQuestion = page.getByRole("group", { name: "I felt calm." });
  await firstTabQuestion.getByRole("radio", { name: "Sometimes" }).check();
  await expect(page.getByText("Ответы сохранены на устройстве.")).toBeVisible();

  const secondTab = await page.context().newPage();
  await secondTab.goto(`/patient/assessments/${SCALE_ID}`);
  const secondTabQuestion = secondTab.getByRole("group", { name: "I felt calm." });
  await expect(secondTabQuestion.getByRole("radio", { name: "Sometimes" })).toBeChecked();
  await secondTabQuestion.getByRole("radio", { name: "Often" }).check();
  await expect(secondTab.getByText("Ответы сохранены на устройстве.")).toBeVisible();

  await firstTabQuestion.getByRole("radio", { name: "Never" }).check();
  await expect(page.getByRole("alert")).toContainText("Ответы изменились в другой вкладке");
  await expect(firstTabQuestion.getByRole("radio", { name: "Often" })).toBeChecked();
  await expect(firstTabQuestion.getByRole("radio", { name: "Never" })).not.toBeChecked();
  expect(api.submissions).toHaveLength(0);

  const storedDraft = await page.evaluate(async (ownerId) => {
    const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
    return (await AssessmentStore.open()).getDraft({ ownerId, scaleId: "synthetic-scale" });
  }, PATIENT_A);
  expect(storedDraft).toMatchObject({ revision: 2, answers: { calm: 5 } });
  await secondTab.close();
});

test("конфликт, затем ещё один клик: предупреждение остаётся, ответы заблокированы до перезагрузки", async ({
  page,
}) => {
  const api = await mockAssessmentApi(page.context());
  await page.goto(`/patient/assessments/${SCALE_ID}`);
  const calm = page.getByRole("group", { name: "I felt calm." });
  const rest = page.getByRole("group", { name: "I could rest." });
  await calm.getByRole("radio", { name: "Sometimes" }).check();
  await expect(page.getByText("Ответы сохранены на устройстве.")).toBeVisible();

  const secondTab = await page.context().newPage();
  await secondTab.goto(`/patient/assessments/${SCALE_ID}`);
  const secondTabCalm = secondTab.getByRole("group", { name: "I felt calm." });
  await expect(secondTabCalm.getByRole("radio", { name: "Sometimes" })).toBeChecked();
  await secondTabCalm.getByRole("radio", { name: "Often" }).check();
  await expect(secondTab.getByText("Ответы сохранены на устройстве.")).toBeVisible();
  await secondTab.close();

  await calm.getByRole("radio", { name: "Never" }).click();
  const conflict = page
    .getByRole("alert")
    .filter({ hasText: "Ответы изменились в другой вкладке" });
  await expect(conflict).toBeVisible();
  await expect(calm.getByRole("radio", { name: "Often" })).toBeChecked();

  await rest.getByRole("radio", { name: "Often" }).click({ force: true });
  await expect(conflict).toBeVisible();
  await expect(page.getByText("Сохраняем ответы на устройстве…")).toHaveCount(0);
  await expect(rest.getByRole("radio", { name: "Often" })).not.toBeChecked();
  for (const radio of await page.getByRole("radio").all()) await expect(radio).toBeDisabled();
  await expect(page.getByRole("button", { name: "Отправить ответы" })).toBeDisabled();
  const storedDraft = await page.evaluate(async (ownerId) => {
    const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
    return (await AssessmentStore.open()).getDraft({ ownerId, scaleId: "synthetic-scale" });
  }, PATIENT_A);
  expect(storedDraft).toMatchObject({ revision: 2, answers: { calm: 5 } });

  await page.getByRole("button", { name: "Перезагрузить страницу" }).click();
  await expect(calm.getByRole("radio", { name: "Often" })).toBeChecked();
  await expect(conflict).toHaveCount(0);
  await rest.getByRole("radio", { name: "Often" }).check();
  await expect(page.getByText("Ответы сохранены на устройстве.")).toBeVisible();
  await page.getByRole("button", { name: "Отправить ответы" }).click();
  await expect(page.getByText("Результат сохранён на сервере.")).toBeVisible();
  expect(api.acknowledgedSubmissions).toHaveLength(1);
  expect(api.acknowledgedSubmissions[0]?.answers).toEqual({ calm: 5, rest: 4 });
});

test("a stale submission from another tab shows a conflict and keeps the newer draft", async ({
  page,
}) => {
  const api = await mockAssessmentApi(page.context());
  await page.goto(`/patient/assessments/${SCALE_ID}`);
  await page
    .getByRole("group", { name: "I felt calm." })
    .getByRole("radio", { name: "Sometimes" })
    .check();
  await page
    .getByRole("group", { name: "I could rest." })
    .getByRole("radio", { name: "Often" })
    .check();
  await expect(page.getByText("Ответы сохранены на устройстве.")).toBeVisible();

  const secondTab = await page.context().newPage();
  await secondTab.goto(`/patient/assessments/${SCALE_ID}`);
  await expect(
    secondTab
      .getByRole("group", { name: "I felt calm." })
      .getByRole("radio", { name: "Sometimes" }),
  ).toBeChecked();
  await expect(
    secondTab.getByRole("group", { name: "I could rest." }).getByRole("radio", { name: "Often" }),
  ).toBeChecked();

  await page
    .getByRole("group", { name: "I felt calm." })
    .getByRole("radio", { name: "Often" })
    .check();
  await expect(page.getByText("Ответы сохранены на устройстве.")).toBeVisible();
  await secondTab.getByRole("button", { name: "Отправить ответы" }).click();

  await expect(secondTab.getByRole("alert")).toContainText("Ответы изменились в другой вкладке");
  expect(api.submissions).toHaveLength(0);
  const storedDraft = await secondTab.evaluate(async (ownerId) => {
    const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
    return (await AssessmentStore.open()).getDraft({ ownerId, scaleId: "synthetic-scale" });
  }, PATIENT_A);
  expect(storedDraft).toMatchObject({ revision: 3, answers: { calm: 5, rest: 4 } });
  await secondTab.close();
});

test("a lost response retries the accepted payload after the scale leaves the catalog", async ({
  page,
}) => {
  const api = await mockAssessmentApi(page.context());
  await page.goto(`/patient/assessments/${SCALE_ID}`);
  await page
    .getByRole("group", { name: "I felt calm." })
    .getByRole("radio", { name: "Sometimes" })
    .check();
  await page
    .getByRole("group", { name: "I could rest." })
    .getByRole("radio", { name: "Often" })
    .check();
  api.loseNextSubmissionResponse();
  await page.getByRole("button", { name: "Отправить ответы" }).click();
  await expect(
    page.getByText("Сохранено на устройстве. Отправим, когда появится связь."),
  ).toBeVisible();
  expect(api.acceptedSubmissions).toHaveLength(1);
  expect(api.acknowledgedSubmissions).toHaveLength(0);

  api.setDefinitions([]);
  api.setNetworkAvailable(false);
  await page.getByRole("link", { name: "Назад" }).click();
  await expect(page.getByText(/Офлайн-режим/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Ожидают отправки" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Повторить отправку" })).toHaveCount(0);

  api.setNetworkAvailable(true);
  await page.evaluate(() => window.dispatchEvent(new Event("online")));

  await expect.poll(() => api.acknowledgedSubmissions.length).toBe(1);
  await expect(page.getByRole("heading", { name: "Ожидают отправки" })).toHaveCount(0);
  await expect(page.getByText("Synthetic calmness band")).toBeVisible();
  expect(api.acceptedSubmissions).toHaveLength(1);
  expect(api.submissions).toHaveLength(2);
  expect(api.submissions[1]).toEqual(api.submissions[0]);
  expect(api.acknowledgedSubmissions[0]).toEqual(api.acceptedSubmissions[0]);
});
