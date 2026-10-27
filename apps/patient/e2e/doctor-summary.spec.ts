import type { Schemas } from "@compass/api-client";
import { type BrowserContext, expect, type Page, test } from "@playwright/test";
import { mockSessionApi, user } from "./session-api-mock";

/**
 * Группа «Сводка с прошлого приёма» в «Обзоре» кабинета врача (спека #38, раздел 3.6;
 * кадры «Врач v4 · 05б» и «05в») на подменённом API. Все данные выдуманы.
 */

type Summary = Schemas["VisitSummaryOut"];

const DOCTOR = user("doctor");
const PATIENT = "00000000-0000-4000-8000-0000000000c1";
const SUMMARY_ID = "00000000-0000-4000-8000-0000000000a1";
const BASE = `/api/v1/patients/${PATIENT}`;

test.use({ timezoneId: "Europe/Moscow" });

const DRAFT: Summary = {
  id: SUMMARY_ID,
  status: "done",
  period_start: "2026-10-05T07:00:00Z",
  period_end: "2026-10-19T07:00:00Z",
  stale: false,
  facts: [
    {
      number: 1,
      kind: "scale",
      at: "2026-10-19T06:12:00Z",
      text: "GAD-7 19.10: 9 баллов, лёгкая; 12.10 было 12, умеренная",
      section: "assessments",
      level: null,
    },
    {
      number: 2,
      kind: "signal",
      at: "2026-10-19T06:14:00Z",
      text: "Сигнал «Обязательно»: мысли о смерти или самоповреждении; открыт",
      section: "signals",
      level: "mandatory",
    },
    {
      number: 3,
      kind: "finding",
      at: "2026-10-18T18:30:00Z",
      text: "Симптом: нарушение сна, голосовая запись в дневнике",
      section: "diary",
      level: null,
    },
    {
      number: 4,
      kind: "finding",
      at: "2026-10-06T07:00:00Z",
      text: "Приём препарата: пропустила вечернюю дозу, сообщение в чате",
      section: "messages",
      level: null,
    },
  ],
  statements: [
    { text: "Тревога снизилась: GAD-7 умеренная → лёгкая", facts: [1] },
    { text: "Открыт сигнал «Обязательно»: мысли о смерти или самоповреждении", facts: [2] },
    { text: "Жалобы на нарушение сна", facts: [3] },
    { text: "Пропустила вечернюю дозу", facts: [4] },
  ],
  edited: false,
  confirmed_by: null,
  confirmed_by_name: null,
  confirmed_at: null,
};

/** `summaryGets` — сколько раз запрошена сводка: каждый запрос — строка журнала доступа. */
type Mock = { summary: Summary; confirmStatus: number; confirmCode?: string; summaryGets?: number };

/** Подмена API «Обзора». Тело подтверждения и повтора записывается. */
async function mockOverview(context: BrowserContext, mock: Mock) {
  const posted: { path: string; body: unknown }[] = [];
  mock.summaryGets = 0;
  await context.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === "POST" && path.endsWith("/confirm")) {
      const body = request.postDataJSON() as { statements: Summary["statements"] };
      posted.push({ path, body });
      if (mock.confirmStatus !== 200) {
        await route.fulfill({
          status: mock.confirmStatus,
          contentType: "application/problem+json",
          json: {
            type: "about:blank",
            title: "Error",
            status: mock.confirmStatus,
            code: mock.confirmCode,
          },
        });
        return;
      }
      mock.summary = {
        ...mock.summary,
        status: "confirmed",
        statements: body.statements,
        edited: true,
        confirmed_by: DOCTOR.id,
        confirmed_by_name: DOCTOR.full_name,
        confirmed_at: "2026-10-19T06:46:00Z",
      };
      await route.fulfill({ json: mock.summary });
      return;
    }
    if (request.method() === "POST" && path.endsWith("/summary/retry")) {
      posted.push({ path, body: null });
      // Повтор поставил расчёт; следующий опрос отдаёт готовый черновик.
      const pending = { ...mock.summary, status: "pending" as const };
      mock.summary = DRAFT;
      await route.fulfill({ json: pending });
      return;
    }
    const json = (() => {
      if (path === `${BASE}/summary`) {
        mock.summaryGets = (mock.summaryGets ?? 0) + 1;
        return mock.summary;
      }
      if (path === BASE) {
        return {
          id: PATIENT,
          full_name: "Лебедева Марина Викторовна",
          email: "p@example.invalid",
          lead_doctor_id: DOCTOR.id,
          created_at: "2026-09-22T09:00:00Z",
          phone: null,
          birth_date: "1992-05-01",
          sex: "female",
        };
      }
      if (path === `${BASE}/assessments`) return { items: [], next_cursor: null };
      if (path.startsWith(BASE) || path === "/api/v1/alerts" || path === "/api/v1/patients") {
        return [];
      }
      return null;
    })();
    if (json === null) {
      await route.fallback();
      return;
    }
    await route.fulfill({ json });
  });
  return posted;
}

async function openOverview(page: Page, mock: Mock) {
  await page.setViewportSize({ width: 1280, height: 900 });
  await mockSessionApi(page.context(), "doctor");
  const posted = await mockOverview(page.context(), mock);
  await page.goto(`/doctor/patients/${PATIENT}`);
  const group = page.getByRole("region", { name: "Сводка с прошлого приёма" });
  await expect(group).toBeVisible();
  return { posted, group };
}

test("doctor edits the drafted summary and confirms it", async ({ page }) => {
  const mock: Mock = { summary: DRAFT, confirmStatus: 200 };
  const { posted, group } = await openOverview(page, mock);

  await expect(group.getByText("с 5 октября")).toBeVisible();
  await expect(group.getByText("Предложение системы")).toBeVisible();
  await expect(group.getByText("ждёт подтверждения врача")).toBeVisible();
  // Уровень сигнала — словом и значком.
  await expect(group.getByText("Обязательно", { exact: true })).toBeVisible();
  await expect(group.locator('img[data-status="required"]')).toBeVisible();
  await expect(group.getByRole("link", { name: "Шкалы" })).toHaveAttribute(
    "href",
    `/doctor/patients/${PATIENT}/scales`,
  );
  await expect(group.getByRole("link", { name: "Сигналы" })).toHaveAttribute(
    "href",
    `/doctor/alerts?patient=${PATIENT}`,
  );
  // Раздела дневника и чата в кабинете ещё нет: подпись без ссылки.
  await expect(group.getByText("Дневник", { exact: true })).toBeVisible();
  await expect(group.getByRole("link", { name: "Дневник" })).toHaveCount(0);
  await page.screenshot({ path: test.info().outputPath("summary-draft-1280.png"), fullPage: true });

  await group.getByRole("button", { name: "Изменить" }).click();
  const sheet = page.getByRole("dialog", { name: "Изменить сводку" });
  await expect(sheet).toBeVisible();
  const confirm = sheet.getByRole("button", { name: "Подтвердить сводку" });
  await sheet.getByRole("textbox", { name: "Утверждение 3" }).fill(" ");
  await expect(confirm).toBeDisabled();
  await expect(
    sheet.getByText("Заполните текст каждого утверждения или удалите его"),
  ).toBeVisible();
  await sheet
    .getByRole("textbox", { name: "Утверждение 3" })
    .fill("Плохо спит: голосовая запись 18.10");
  await sheet.getByRole("button", { name: "Удалить: утверждение 4" }).click();
  await expect(sheet.getByRole("textbox", { name: "Утверждение 4" })).toHaveCount(0);
  // Лист появляется проявлением 150 мс: снимок — после конца анимации.
  await sheet.evaluate((el) =>
    Promise.all(el.getAnimations({ subtree: true }).map((a) => a.finished)),
  );
  await page.screenshot({ path: test.info().outputPath("summary-edit-1280.png"), fullPage: true });
  await confirm.click();

  await expect(sheet).toBeHidden();
  expect(posted).toEqual([
    {
      path: `${BASE}/summary/${SUMMARY_ID}/confirm`,
      body: {
        statements: [
          { text: "Тревога снизилась: GAD-7 умеренная → лёгкая", facts: [1] },
          {
            text: "Открыт сигнал «Обязательно»: мысли о смерти или самоповреждении",
            facts: [2],
          },
          { text: "Плохо спит: голосовая запись 18.10", facts: [3] },
        ],
      },
    },
  ]);
  await expect(group.getByText("Подтверждено врачом")).toBeVisible();
  await expect(group.getByText("Изменено врачом")).toBeVisible();
  await expect(group.getByText("вы, 19.10, 09:46")).toBeVisible();
  await expect(group.getByText("Плохо спит: голосовая запись 18.10")).toBeVisible();
  await expect(group.getByText("Пропустила вечернюю дозу")).toHaveCount(0);
  await expect(group.getByRole("button", { name: "Подтвердить сводку" })).toHaveCount(0);
  await page.screenshot({
    path: test.info().outputPath("summary-confirmed-1280.png"),
    fullPage: true,
  });
});

test("editing keeps the original draft when a new summary arrives", async ({ page }) => {
  await page.clock.install();
  const mock: Mock = { summary: DRAFT, confirmStatus: 409, confirmCode: "stale" };
  const { posted, group } = await openOverview(page, mock);
  await group.getByRole("button", { name: "Изменить" }).click();
  const sheet = page.getByRole("dialog", { name: "Изменить сводку" });
  await sheet.getByRole("textbox", { name: "Утверждение 1" }).fill("Правка прежнего черновика");
  const before = mock.summaryGets ?? 0;
  mock.summary = {
    ...DRAFT,
    id: "00000000-0000-4000-8000-0000000000a2",
    facts: DRAFT.facts.map((fact) => ({ ...fact, text: "Новый результат: 18 баллов" })),
    statements: [{ text: "Новый результат", facts: [1] }],
  };
  await page.clock.fastForward(31_000);
  // focusManager слушает window; синтетическое событие document без bubbles туда не доходит.
  await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
  await expect.poll(() => mock.summaryGets ?? 0).toBeGreaterThan(before);
  await expect(group.getByText("Новый результат", { exact: true })).toBeVisible();
  await expect(sheet.getByRole("textbox", { name: "Утверждение 1" })).toHaveValue(
    "Правка прежнего черновика",
  );
  await expect(sheet.getByText("Новый результат: 18 баллов", { exact: false })).toHaveCount(0);
  await sheet.getByRole("button", { name: "Подтвердить сводку" }).click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]?.path).toBe(`${BASE}/summary/${SUMMARY_ID}/confirm`);
});

for (const [status, code, text] of [
  [409, "stale", "Данные пациента изменились. Сводка обновляется, правку нужно будет повторить."],
  [422, "unknown_fact", "Не удалось подтвердить сводку, обновите её"],
] as const) {
  test(`confirm answered ${status} ${code} asks to refresh the summary`, async ({ page }) => {
    const mock: Mock = { summary: DRAFT, confirmStatus: status, confirmCode: code };
    const { group } = await openOverview(page, mock);

    await group.getByRole("button", { name: "Подтвердить сводку" }).click();

    await expect(group.getByText(text)).toBeVisible();
  });
}

test("no new data since the previous visit", async ({ page }) => {
  const empty: Summary = { ...DRAFT, id: null, status: "empty", facts: [], statements: [] };
  const { group } = await openOverview(page, { summary: empty, confirmStatus: 200 });

  await expect(group.getByText("Нет новых данных с 5 октября")).toBeVisible();
  await expect(group.getByRole("button")).toHaveCount(0);
});

test("failed draft can be retried and then appears", async ({ page }) => {
  const failed: Summary = { ...DRAFT, status: "failed", statements: [] };
  const { posted, group } = await openOverview(page, { summary: failed, confirmStatus: 200 });

  await expect(group.getByText("Не удалось составить сводку")).toBeVisible();
  // Остальные группы «Обзора» работают.
  await expect(page.getByRole("region", { name: "Состояние" })).toBeVisible();
  await group.getByRole("button", { name: "Повторить" }).click();

  await expect.poll(() => posted.map((p) => p.path)).toEqual([`${BASE}/summary/retry`]);
  // Ответ повтора — расчёт; опрос раз в 3 с приносит черновик.
  await expect(group.getByText("Жалобы на нарушение сна")).toBeVisible({ timeout: 10_000 });
});

// Мак с моделью выключен: черновик не считается. Опрос раз в 3 с идёт не дольше 3 минут,
// дальше группа предлагает «Обновить», и запросы (строки журнала доступа) прекращаются.
test("polling stops after 3 minutes and offers «Обновить»", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-10-24T09:00:00Z") });
  const pending: Summary = { ...DRAFT, status: "pending", statements: [] };
  const mock: Mock = { summary: pending, confirmStatus: 200 };
  const { group } = await openOverview(page, mock);
  await expect(group.getByRole("status").getByText("Система составляет сводку")).toBeAttached();

  await page.clock.fastForward(181_000);

  await expect(group.getByText("Сводка ещё составляется")).toBeVisible();
  const gets = mock.summaryGets ?? 0;
  await page.clock.runFor(30_000);
  expect(mock.summaryGets).toBe(gets);

  await group.getByRole("button", { name: "Обновить" }).click();
  await expect.poll(() => mock.summaryGets).toBe(gets + 1);
});
