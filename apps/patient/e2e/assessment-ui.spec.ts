import type { Schemas } from "@compass/api-client";
import { expect, test } from "@playwright/test";
import { mockAssessmentApi } from "./assessment-api-mock";
import { DEFINITION_FORM, DEFINITION_V2, PATIENT_A, SCALE_ID } from "./assessment-fixtures";

test("catalog builds a dynamic form and confirms only a server-saved result", async ({ page }) => {
  const api = await mockAssessmentApi(page.context());
  await page.setViewportSize({ width: 360, height: 800 });
  await page.goto("/patient/");

  await expect(page.getByRole("heading", { name: "Опросники" })).toBeVisible();
  await expect(page.getByText(DEFINITION_FORM.scale.title)).toBeVisible();
  await expect(page.getByText("Международный, дополнительный источник")).toBeVisible();

  await page.getByRole("link", { name: "Заполнить" }).click();
  await expect(page.getByRole("heading", { name: DEFINITION_FORM.scale.title })).toBeVisible();
  const calm = page.getByRole("group", { name: "I felt calm." });
  const rest = page.getByRole("group", { name: "I could rest." });
  await calm.getByRole("radio", { name: "Sometimes" }).check();
  await rest.getByRole("radio", { name: "Often" }).check();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );

  await page.getByRole("button", { name: "Отправить ответы" }).click();
  await expect(page.getByText("Результат сохранён на сервере")).toBeVisible();
  expect(api.submissions).toHaveLength(1);
  expect(api.acknowledgedSubmissions).toHaveLength(1);
  expect(api.submissions[0]).toMatchObject({
    patient_id: "00000000-0000-4000-8000-0000000000a1",
    scale_id: SCALE_ID,
    scale_version: DEFINITION_FORM.scale.version,
    answers: { calm: 2, rest: 4 },
  });
  expect(api.acknowledgedSubmissions[0]).toEqual(api.submissions[0]);
});

test("an incomplete form explains the error and focuses the first unanswered item", async ({
  page,
}) => {
  await mockAssessmentApi(page.context());
  await page.goto(`/patient/assessments/${SCALE_ID}`);
  await expect(page.getByRole("group", { name: "I felt calm." })).toBeVisible();

  await page
    .getByRole("group", { name: "I felt calm." })
    .getByRole("radio", { name: "Never" })
    .check();
  await page.getByRole("button", { name: "Отправить ответы" }).click();

  await expect(page.getByRole("alert")).toContainText("Ответьте на все вопросы");
  await expect(
    page.getByRole("group", { name: "I could rest." }).getByRole("radio").first(),
  ).toBeFocused();
});

test("history shows every saved score, both dates, sources, separate versions and the next page", async ({
  page,
}) => {
  const history: Schemas["AssessmentOut"][] = [1, 2].map((version, index) => ({
    id: `00000000-0000-4000-8000-00000000003${index}`,
    patient_id: "00000000-0000-4000-8000-0000000000a1",
    submission_id: `00000000-0000-4000-8000-00000000004${index}`,
    scale_id: SCALE_ID,
    scale_title: DEFINITION_FORM.scale.title,
    scale_version: version,
    completed_at: `2026-10-1${index + 2}T10:00:00.000Z`,
    received_at: `2026-10-1${index + 2}T10:05:00.000Z`,
    answers: { calm: index + 1, rest: index + 2 },
    scores: {
      calmness: {
        value: index + 1,
        band: {
          code: "synthetic",
          label: `Synthetic calmness ${version}`,
          source: {
            id: "synthetic-validation",
            title: "Synthetic validation source",
            url: "https://example.invalid/validation",
            locator: "section 1",
            supplementary: true,
          },
        },
      },
      rest: {
        value: index + 2,
        band: {
          code: "synthetic",
          label: `Synthetic rest ${version}`,
          source: {
            id: "synthetic-validation",
            title: "Synthetic validation source",
            url: "https://example.invalid/validation",
            locator: "section 1",
            supplementary: true,
          },
        },
      },
    },
    sources: [
      {
        id: "synthetic-validation",
        title: "Synthetic validation source",
        url: "https://example.invalid/validation",
        supplementary: true,
      },
    ],
  }));
  await mockAssessmentApi(page.context(), { history });
  await page.goto("/patient/");

  await expect(page.getByRole("heading", { name: "Динамика" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Версия 2", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Версия 1", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Загрузить ещё" })).toBeVisible();
  await page.getByRole("button", { name: "Загрузить ещё" }).click();
  await expect(page.getByRole("heading", { name: "Версия 1", exact: true })).toBeVisible();
  await expect(page.getByText("Synthetic calmness 1")).toBeVisible();
  await expect(page.getByText("Synthetic calmness 2")).toBeVisible();
  await expect(page.getByText("Дата заполнения")).toBeVisible();
  await expect(page.getByText("Дата получения сервером")).toBeVisible();
  await expect(page.getByRole("table").getByText("calmness").first()).toBeVisible();
  expect(await page.getByRole("img", { name: /график/i }).count()).toBeGreaterThanOrEqual(4);
});

test("offline queued answers survive reload and remain separate from server history", async ({
  page,
}) => {
  const api = await mockAssessmentApi(page.context());
  await page.goto("/patient/");
  await page.getByRole("link", { name: "Заполнить" }).click();
  await page
    .getByRole("group", { name: "I felt calm." })
    .getByRole("radio", { name: "Sometimes" })
    .check();
  await page
    .getByRole("group", { name: "I could rest." })
    .getByRole("radio", { name: "Often" })
    .check();

  api.setNetworkAvailable(false);
  await page.getByRole("button", { name: "Отправить ответы" }).click();
  await expect(
    page.getByText("Сохранено на устройстве. Отправим, когда появится связь."),
  ).toBeVisible();
  await expect(page.getByText("Результат сохранён на сервере")).toHaveCount(0);
  expect(api.submissions).toHaveLength(0);

  await page.reload();
  await expect(page.getByRole("heading", { name: DEFINITION_FORM.scale.title })).toBeVisible();
  await page.getByRole("link", { name: "Назад" }).click();
  await expect(page.getByText(/Офлайн-режим/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Ожидают отправки" })).toBeVisible();
  await expect(page.getByText("I felt calm. — Sometimes")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Динамика" })).toHaveCount(0);
  expect(api.submissions).toHaveLength(0);
});

test("restoring the network after offline reload revalidates the session and sends once", async ({
  page,
}) => {
  const api = await mockAssessmentApi(page.context());
  await page.goto("/patient/");
  await page.getByRole("link", { name: "Заполнить" }).click();
  await page
    .getByRole("group", { name: "I felt calm." })
    .getByRole("radio", { name: "Sometimes" })
    .check();
  await page
    .getByRole("group", { name: "I could rest." })
    .getByRole("radio", { name: "Often" })
    .check();
  api.setNetworkAvailable(false);
  await page.getByRole("button", { name: "Отправить ответы" }).click();
  await expect(
    page.getByText("Сохранено на устройстве. Отправим, когда появится связь."),
  ).toBeVisible();

  await page.reload();
  await expect(page.getByRole("heading", { name: DEFINITION_FORM.scale.title })).toBeVisible();
  await page.getByRole("link", { name: "Назад" }).click();
  await expect(page.getByText(/Офлайн-режим/)).toBeVisible();
  api.setNetworkAvailable(true);
  await page.evaluate(() => window.dispatchEvent(new Event("online")));

  await expect.poll(() => api.acknowledgedSubmissions.length).toBe(1);
  await expect(page.getByText("Synthetic calmness band")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Ожидают отправки" })).toHaveCount(0);
});

test("a 401 keeps the queue for patient A, hides it from B, and resumes after A signs in", async ({
  page,
}) => {
  const api = await mockAssessmentApi(page.context());
  await page.goto("/patient/");
  await page.getByRole("link", { name: "Заполнить" }).click();
  await page
    .getByRole("group", { name: "I felt calm." })
    .getByRole("radio", { name: "Sometimes" })
    .check();
  await page
    .getByRole("group", { name: "I could rest." })
    .getByRole("radio", { name: "Often" })
    .check();

  api.setNetworkAvailable(false);
  await page.getByRole("button", { name: "Отправить ответы" }).click();
  await expect(
    page.getByText("Сохранено на устройстве. Отправим, когда появится связь."),
  ).toBeVisible();
  await page.getByRole("link", { name: "Назад" }).click();
  await expect(page.getByRole("heading", { name: "Ожидают отправки" })).toBeVisible();

  api.setNetworkAvailable(true);
  api.setAuthenticatedUser(null);
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect(page.getByLabel("Электронная почта", { exact: true })).toBeVisible();
  expect(api.submissions).toHaveLength(0);

  await page.getByLabel("Электронная почта", { exact: true }).fill("patient-b@example.invalid");
  await page.getByLabel("Пароль", { exact: true }).fill("synthetic-password");
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.getByRole("heading", { name: /Синтетический пациент Б/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Ожидают отправки" })).toHaveCount(0);
  await expect(page.getByText("I felt calm. — Sometimes")).toHaveCount(0);
  expect(api.submissions).toHaveLength(0);

  await page.getByRole("button", { name: "Выйти" }).click();
  await expect(page.getByLabel("Электронная почта", { exact: true })).toBeVisible();
  await page.getByLabel("Электронная почта", { exact: true }).fill("patient-a@example.invalid");
  await page.getByLabel("Пароль", { exact: true }).fill("synthetic-password");
  await page.getByRole("button", { name: "Войти" }).click();

  await expect.poll(() => api.acknowledgedSubmissions.length).toBe(1);
  expect(api.submissions).toHaveLength(1);
  expect(api.submissions[0]?.patient_id).toBe(PATIENT_A);
});

test("logout with a saved draft defaults to keeping it and deletes only after explicit confirmation", async ({
  page,
}) => {
  await mockAssessmentApi(page.context());
  await page.goto("/patient/");
  await page.getByRole("link", { name: "Заполнить" }).click();
  await page
    .getByRole("group", { name: "I felt calm." })
    .getByRole("radio", { name: "Sometimes" })
    .check();
  await expect(page.getByRole("status")).toContainText("Ответы сохранены на устройстве");
  await page.getByRole("link", { name: "Назад" }).click();

  await page.getByRole("button", { name: "Выйти" }).click();
  const confirm = page.getByRole("alertdialog", { name: "Выход с локальными ответами" });
  await expect(confirm).toContainText("1 черновик");
  const stay = confirm.getByRole("button", { name: "Остаться" });
  await expect(stay).toBeFocused();
  await stay.click();
  await expect(confirm).toHaveCount(0);

  await page.getByRole("link", { name: "Заполнить" }).click();
  await expect(
    page.getByRole("group", { name: "I felt calm." }).getByRole("radio", { name: "Sometimes" }),
  ).toBeChecked();
  await page.getByRole("link", { name: "Назад" }).click();
  await page.getByRole("button", { name: "Выйти" }).click();
  await page
    .getByRole("alertdialog", { name: "Выход с локальными ответами" })
    .getByRole("button", { name: "Удалить локальные ответы и выйти" })
    .click();

  await expect(page.getByLabel("Электронная почта", { exact: true })).toBeVisible();
  const ownerPointer = await page.evaluate(async () => {
    const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
    return (await AssessmentStore.open()).getOwnerPointer();
  });
  expect(ownerPointer).toBeNull();
});

test("a version conflict keeps its original answers and requires confirmation before deletion", async ({
  page,
}) => {
  const api = await mockAssessmentApi(page.context());
  await page.goto("/patient/");
  await page.getByRole("link", { name: "Заполнить" }).click();
  await page
    .getByRole("group", { name: "I felt calm." })
    .getByRole("radio", { name: "Sometimes" })
    .check();
  await page
    .getByRole("group", { name: "I could rest." })
    .getByRole("radio", { name: "Often" })
    .check();

  api.setNextSubmissionResponse(409, "scale_version_changed");
  await page.getByRole("button", { name: "Отправить ответы" }).click();
  await expect(
    page.getByText("Сохранено на устройстве. Отправим, когда появится связь."),
  ).toBeVisible();
  expect(api.acknowledgedSubmissions).toHaveLength(0);

  api.setDefinitions([DEFINITION_V2]);
  await page.getByRole("link", { name: "Назад" }).click();
  await expect(page.getByRole("heading", { name: "Нужно проверить" })).toBeVisible();
  await expect(page.getByText(/версия опросника изменилась/)).toBeVisible();
  await expect(page.getByText("I felt calm.")).toBeVisible();
  await expect(page.getByText("Sometimes", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Заполнить актуальную версию" })).toBeVisible();
  await page.getByRole("link", { name: "Заполнить актуальную версию" }).click();
  await expect(page.getByRole("heading", { name: "Synthetic scale" })).toBeVisible();
  await expect(page.getByRole("group", { name: "New synthetic question" })).toBeVisible();
  await expect(
    page.getByRole("group", { name: "New synthetic question" }).getByRole("radio"),
  ).toHaveCount(1);
  await expect(
    page.getByRole("group", { name: "New synthetic question" }).getByRole("radio"),
  ).not.toBeChecked();
  await page.getByRole("link", { name: "Назад" }).click();

  await page.getByRole("button", { name: "Удалить конфликтный ответ" }).click();
  const confirm = page.getByRole("alertdialog", { name: "Удаление сохранённого ответа" });
  await expect(confirm).toContainText("I felt calm.");
  await expect(confirm.getByRole("button", { name: "Оставить ответ" })).toBeFocused();
  await confirm.getByRole("button", { name: "Оставить ответ" }).click();
  await expect(page.getByText("I felt calm.")).toBeVisible();
  expect(api.acknowledgedSubmissions).toHaveLength(0);

  await page.getByRole("button", { name: "Удалить конфликтный ответ" }).click();
  await page
    .getByRole("alertdialog", { name: "Удаление сохранённого ответа" })
    .getByRole("button", { name: "Удалить ответ" })
    .click();
  await expect(page.getByRole("heading", { name: "Нужно проверить" })).toHaveCount(0);
  await expect(page.getByText("I felt calm.")).toHaveCount(0);
  expect(api.acknowledgedSubmissions).toHaveLength(0);
});

test("a failed local transaction leaves the form answers visible and never confirms a result", async ({
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

  await page.getByRole("button", { name: "Отправить ответы" }).click();
  await expect(page.getByRole("alert")).toContainText("Не удалось сохранить ответ на устройстве");
  await expect(page.getByRole("heading", { name: DEFINITION_FORM.scale.title })).toBeVisible();
  await expect(
    page.getByRole("group", { name: "I felt calm." }).getByRole("radio", { name: "Sometimes" }),
  ).toBeChecked();
  await expect(
    page.getByRole("group", { name: "I could rest." }).getByRole("radio", { name: "Often" }),
  ).toBeChecked();
  await expect(page.getByText("Результат сохранён на сервере")).toHaveCount(0);
  expect(api.acknowledgedSubmissions).toHaveLength(0);
});
