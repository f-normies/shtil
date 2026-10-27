import { type BrowserContext, expect, type Page, test } from "@playwright/test";
import { LEBEDEVA, openWindow } from "./doctor-window-mock";
import { auditScreen } from "./signal-audit";

/**
 * Форма шкалы врача (спека #94, форма; кадр «Врач v4 · 09») на подменённом API.
 * Шкала — сокращённая HAMD-17 из трёх пунктов с ключом КР; данные выдуманы.
 */

const REF = { source: "kr-depression-2024", locator: "прил. Г1" };
const option = (value: number, text: string) => ({ value, text });

const HAMD = {
  scale: {
    id: "hamd-17",
    title: "Шкала Гамильтона для оценки депрессии (HAMD-17)",
    instruction: "Период оценки — неделя, предшествующая опросу.",
    version: 1,
    purpose: "monitoring",
    respondent: "clinician",
    status: "reviewed",
    license: { terms: "t", allowed: true, source: REF },
    validation: [REF],
    items: [
      {
        id: "q1",
        text: "Сниженное настроение",
        options: [
          option(0, "Отсутствует"),
          option(1, "Только при расспросе"),
          option(4, "Только эти чувства"),
        ],
      },
      {
        id: "q2",
        text: "Чувство вины",
        options: [option(0, "Отсутствует"), option(4, "Галлюцинации обвиняющего содержания")],
      },
      {
        id: "q3",
        text: "Суицидальные намерения",
        options: [option(0, "Отсутствует"), option(4, "Попытки самоубийства")],
      },
    ],
    scores: [
      {
        id: "total",
        items: ["q1", "q2", "q3"],
        bands: [
          { min: 0, max: 7, code: "normal", label: "Норма", source: REF },
          { min: 8, max: 12, code: "mild", label: "Лёгкое депрессивное расстройство", source: REF },
        ],
      },
    ],
  },
  sources: [],
};

/** Каталог шкал врача и отправка результата. `status` — ответ на отправку. */
async function mockForm(context: BrowserContext, posted: unknown[], status = 201) {
  await context.route("**/api/v1/assessments/clinician-scales", (route) =>
    route.fulfill({ json: { items: [HAMD] } }),
  );
  await context.route(`**/api/v1/patients/${LEBEDEVA}/assessments`, async (route) => {
    if (route.request().method() !== "POST") {
      await route.fallback();
      return;
    }
    posted.push(route.request().postDataJSON());
    await route.fulfill(
      status === 201
        ? { status, json: { id: "r-new" } }
        : { status, json: { status, title: "Unavailable" } },
    );
  });
}

async function openForm(page: Page, posted: unknown[], status = 201) {
  await openWindow(page, `/doctor/patients/${LEBEDEVA}/scales`, (context) =>
    mockForm(context, posted, status),
  );
  await page.getByRole("button", { name: "Заполнить HAMD-17" }).click();
  const sheet = page.getByRole("dialog", { name: /HAMD-17/ });
  await expect(sheet).toBeVisible();
  return sheet;
}

test("doctor fills HAMD-17 item by item and saves one result", async ({ page }, testInfo) => {
  const posted: unknown[] = [];
  const sheet = await openForm(page, posted);

  await expect(sheet.getByText("Период оценки — неделя, предшествующая опросу.")).toBeVisible();
  await expect(sheet.getByText("Отвечено 0 из 3")).toBeVisible();
  const save = sheet.getByRole("button", { name: "Сохранить результат" });
  await expect(save).toBeDisabled();

  await sheet.getByRole("radio", { name: /Только эти чувства/ }).check();
  await sheet.getByRole("radio", { name: /Галлюцинации обвиняющего содержания/ }).check();
  await expect(sheet.getByText("Отвечено 2 из 3")).toBeVisible();
  await sheet
    .getByRole("group", { name: /Суицидальные намерения/ })
    .getByRole("radio", { name: /Отсутствует/ })
    .check();
  await expect(sheet.getByText("Сумма 8 из 12, лёгкое депрессивное расстройство")).toBeVisible();
  await auditScreen(page, testInfo, { cabinet: "doctor", name: "09-shkaly-vrach-forma" });

  await save.click();
  await expect(sheet).toBeHidden();
  expect(posted).toHaveLength(1);
  const body = posted[0] as Record<string, unknown>;
  expect(body).toMatchObject({
    scale_id: "hamd-17",
    scale_version: 1,
    answers: { q1: 4, q2: 4, q3: 0 },
  });
  expect(String(body.submission_id)).toMatch(/^[0-9a-f-]{36}$/);
});

test("a failed save keeps the answers and says so in words", async ({ page }) => {
  const posted: unknown[] = [];
  const sheet = await openForm(page, posted, 503);

  await sheet.getByRole("radio", { name: /Только эти чувства/ }).check();
  await sheet.getByRole("radio", { name: /Галлюцинации обвиняющего содержания/ }).check();
  await sheet
    .getByRole("group", { name: /Суицидальные намерения/ })
    .getByRole("radio", { name: /Отсутствует/ })
    .check();
  await sheet.getByRole("button", { name: "Сохранить результат" }).click();

  await expect(
    sheet.getByText("Результат не сохранён. Проверьте связь и попробуйте ещё раз."),
  ).toBeVisible();
  await expect(sheet.getByRole("radio", { name: /Только эти чувства/ })).toBeChecked();
});

async function fillInterview(page: Page) {
  const sheet = page.getByRole("dialog", { name: /HAMD-17/ });
  await sheet.getByRole("radio", { name: /Только эти чувства/ }).check();
  await sheet.getByRole("radio", { name: /Галлюцинации обвиняющего содержания/ }).check();
  await sheet
    .getByRole("group", { name: /Суицидальные намерения/ })
    .getByRole("radio", { name: /Отсутствует/ })
    .check();
  await sheet.getByRole("button", { name: "Сохранить результат" }).click();
  return sheet;
}

test("a changed scale can be reloaded and submitted with its new version", async ({ page }) => {
  const posted: Record<string, unknown>[] = [];
  await openWindow(page, `/doctor/patients/${LEBEDEVA}/scales`, async (context) => {
    await context.route("**/api/v1/assessments/clinician-scales", (route) =>
      route.fulfill({
        json: {
          items: [
            {
              ...HAMD,
              scale: {
                ...HAMD.scale,
                version: posted.length ? 2 : 1,
                instruction: posted.length
                  ? "Обновлённая инструкция интервью"
                  : HAMD.scale.instruction,
              },
            },
          ],
        },
      }),
    );
    await context.route(`**/api/v1/patients/${LEBEDEVA}/assessments`, async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      const body = route.request().postDataJSON() as Record<string, unknown>;
      posted.push(body);
      return body.scale_version === 1
        ? route.fulfill({
            status: 409,
            json: {
              type: "about:blank",
              title: "Conflict",
              status: 409,
              code: "scale_version_changed",
              current_version: 2,
            },
          })
        : route.fulfill({ status: 201, json: { id: "r-new" } });
    });
  });
  await page.getByRole("button", { name: "Заполнить HAMD-17" }).click();
  const sheet = await fillInterview(page);
  await sheet.getByRole("button", { name: "Обновить шкалу" }).click();
  await expect(sheet.getByText("Обновлённая инструкция интервью")).toBeVisible();
  await expect(sheet.getByText("Отвечено 0 из 3")).toBeVisible();
  await fillInterview(page);
  await expect(sheet).toBeHidden();
  expect(posted).toHaveLength(2);
  expect(posted[1]?.scale_version).toBe(2);
  expect(posted[1]?.submission_id).not.toBe(posted[0]?.submission_id);
});

for (const [code, message] of [
  ["submission_conflict", /Эта отправка уже сохранена/],
  ["lead_doctor_missing", /У пациента нет ведущего врача/],
] as const) {
  test(`409 ${code} explains the actual conflict and keeps answers`, async ({ page }) => {
    const sheet = await openForm(page, [], 409);
    await page.context().route(`**/api/v1/patients/${LEBEDEVA}/assessments`, (route) =>
      route.request().method() === "POST"
        ? route.fulfill({
            status: 409,
            json: {
              type: "about:blank",
              title: "Conflict",
              status: 409,
              code,
            },
          })
        : route.fallback(),
    );
    await fillInterview(page);
    await expect(sheet.getByRole("alert")).toContainText(message);
    await expect(sheet.getByRole("button", { name: "Обновить шкалу" })).toHaveCount(0);
    await expect(sheet.getByRole("radio", { name: /Только эти чувства/ })).toBeChecked();
  });
}
