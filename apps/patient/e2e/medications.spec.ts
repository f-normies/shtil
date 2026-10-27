import type { Schemas } from "@compass/api-client";
import { type BrowserContext, expect, type Page, test } from "@playwright/test";
import { mockSessionApi } from "./session-api-mock";

/**
 * «Лекарства на сегодня» (спека #34, раздел 7; кадр «Пациент · 16») на подменённом API.
 * Препарат выдуман. Касание квадрата сразу сохраняет отметку; «Готово» с 17:00 по Москве
 * сохраняет как пропущенные пустые приёмы, время которых пришло (`due` от сервера).
 */

const PRESCRIPTION = "00000000-0000-4000-8000-0000000000e1";
const DAY = "2026-10-18";

function dose(slot: number, label: string, due = true): Schemas["DoseOut"] {
  return {
    prescription_id: PRESCRIPTION,
    trade_name: "Синтетин",
    dose_mg: 20,
    slot,
    slot_label: label,
    status: null,
    marked_at: null,
    due,
  };
}

/** Сервер хранит отметки и отдаёт дозы дня с ними. */
async function mockDoses(
  context: BrowserContext,
  doses: Schemas["DoseOut"][] = [dose(0, "Утро"), dose(1, "Вечер")],
) {
  const puts: Schemas["MarksIn"][] = [];
  await context.route("**/api/v1/me/doses**", async (route) => {
    const request = route.request();
    if (request.method() === "PUT") {
      const body = request.postDataJSON() as Schemas["MarksIn"];
      puts.push(body);
      for (const m of body.marks) {
        const d = doses[m.slot];
        if (d) {
          d.status = m.status ?? null;
          d.marked_at = m.status ? "2026-10-18T05:40:00Z" : null;
        }
      }
    }
    await route.fulfill({ status: 200, json: { day: DAY, doses } });
  });
  return puts;
}

async function open(page: Page, moscow: string): Promise<void> {
  await page.clock.setFixedTime(new Date(`${DAY}T${moscow}:00+03:00`));
  await page.setViewportSize({ width: 360, height: 780 });
  await page.goto("/patient/");
  await page.getByRole("link", { name: "Отметить приём" }).click();
  await expect(page.getByRole("heading", { name: "Лекарства на сегодня" })).toBeVisible();
}

test("patient marks and clears a dose at 360 px; before evening nothing becomes missed", async ({
  page,
}, testInfo) => {
  await mockSessionApi(page.context(), "patient");
  const puts = await mockDoses(page.context());
  await open(page, "09:00");

  const morning = page.getByRole("checkbox", { name: /Синтетин 20 мг, утром/ });
  await morning.check();
  await expect(page.getByText("Утром, принято в 08:40")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  // Без анимации флажка: снимок не попадает на середину перехода цвета.
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.screenshot({ path: testInfo.outputPath("16-medications.png"), fullPage: true });
  await page.getByRole("checkbox", { name: /Синтетин 20 мг, утром, принято/ }).uncheck();
  await expect(page.getByText("Утром", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "На главную" }).click();

  await expect(page).toHaveURL(/\/patient\/$/);
  expect(puts.map((p) => p.marks.map((m) => [m.slot, m.status]))).toEqual([
    [[0, "taken"]],
    [[0, null]],
  ]);
});

test("in the evening «Готово» saves empty doses as missed", async ({ page }) => {
  await mockSessionApi(page.context(), "patient");
  const puts = await mockDoses(page.context());
  await open(page, "21:04");

  await page.getByRole("checkbox", { name: /утром/ }).check();
  await expect(
    page.getByText("Пустые приёмы, время которых прошло, сохранятся как пропущенные"),
  ).toBeVisible();
  await page.getByRole("button", { name: "Готово" }).click();

  await expect(page).toHaveURL(/\/patient\/$/);
  expect(puts.at(-1)).toEqual({
    marks: [{ prescription_id: PRESCRIPTION, day: DAY, slot: 1, status: "missed" }],
  });
});

test("«Готово» before the evening dose time leaves it unmarked", async ({ page }) => {
  // Ревью PR #158: в 17:30 вечерний приём ещё впереди — в пропущенные уходит только утро.
  await mockSessionApi(page.context(), "patient");
  const puts = await mockDoses(page.context(), [dose(0, "Утро"), dose(1, "Вечер", false)]);
  await open(page, "17:30");

  await page.getByRole("button", { name: "Готово" }).click();

  await expect(page).toHaveURL(/\/patient\/$/);
  expect(puts).toEqual([
    { marks: [{ prescription_id: PRESCRIPTION, day: DAY, slot: 0, status: "missed" }] },
  ]);
});

test("home has no medications card without doses today", async ({ page }) => {
  // Спека #34, раздел 7: карточка — только если сегодня есть дозы.
  await mockSessionApi(page.context(), "patient");
  await mockDoses(page.context(), []);
  await page.goto("/patient/");

  await expect(page.getByRole("heading", { name: "Моя терапия" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Отметить приём" })).toHaveCount(0);
});

test("network error is said in words", async ({ page }) => {
  await mockSessionApi(page.context(), "patient");
  await mockDoses(page.context());
  await page.context().route("**/api/v1/me/doses", async (route) => {
    if (route.request().method() === "PUT") {
      await route.abort();
      return;
    }
    await route.fallback();
  });
  await open(page, "09:00");

  await page.getByRole("checkbox", { name: /утром/ }).check();

  await expect(
    page.getByText("Не удалось отправить. Проверьте связь и попробуйте ещё раз."),
  ).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /утром/ })).not.toBeChecked();
});

test("finishing rereads which doses are due after the page was opened", async ({ page }) => {
  await mockSessionApi(page.context(), "patient");
  const puts: Schemas["MarksIn"][] = [];
  let eveningDue = false;
  await page.context().route("**/api/v1/me/doses**", async (route) => {
    if (route.request().method() === "PUT") puts.push(route.request().postDataJSON());
    await route.fulfill({
      json: {
        day: DAY,
        doses: [
          { ...dose(0, "Утро"), status: "taken", marked_at: `${DAY}T05:40:00Z` },
          dose(1, "Вечер", eveningDue),
        ],
      },
    });
  });
  await open(page, "18:59");
  await expect(page.getByRole("checkbox", { name: /утром/ })).toBeChecked();
  eveningDue = true;
  await page.clock.setFixedTime(new Date(`${DAY}T19:01:00+03:00`));

  await page.getByRole("button", { name: "Готово" }).click();

  await expect(page).toHaveURL(/\/patient\/$/);
  expect(puts).toEqual([
    { marks: [{ prescription_id: PRESCRIPTION, day: DAY, slot: 1, status: "missed" }] },
  ]);
});

test("an open medications page switches to the new Moscow day before marking", async ({ page }) => {
  await mockSessionApi(page.context(), "patient");
  await page.clock.install({ time: new Date(`${DAY}T23:59:00+03:00`) });
  let day = DAY;
  const puts: Schemas["MarksIn"][] = [];
  await page.context().route("**/api/v1/me/doses**", async (route) => {
    if (route.request().method() === "PUT") puts.push(route.request().postDataJSON());
    await route.fulfill({
      json: {
        day,
        doses: [
          {
            ...dose(0, "Утро"),
            status:
              puts.at(-1)?.marks.find((m) => m.day === day)?.status ??
              (day === DAY ? "taken" : null),
          },
        ],
      },
    });
  });
  await page.goto("/patient/medications");
  const morning = page.getByRole("checkbox", { name: /утром/ });
  await expect(morning).toBeChecked();
  day = "2026-10-19";
  await page.clock.fastForward("02:00");

  await expect(morning).not.toBeChecked();
  await morning.check();
  await expect.poll(() => puts.length).toBe(1);
  expect(puts[0]).toEqual({
    marks: [{ prescription_id: PRESCRIPTION, day: "2026-10-19", slot: 0, status: "taken" }],
  });
});

test("a dose refresh that crosses midnight cannot save yesterday's mark", async ({ page }) => {
  await mockSessionApi(page.context(), "patient");
  await page.clock.setFixedTime(new Date(`${DAY}T23:59:00+03:00`));
  const puts: Schemas["MarksIn"][] = [];
  let blockNextRead = false;
  let release = () => {};
  let started = () => {};
  const blocked = new Promise<void>((resolve) => {
    release = resolve;
  });
  const refreshing = new Promise<void>((resolve) => {
    started = resolve;
  });
  await page.context().route("**/api/v1/me/doses**", async (route) => {
    if (route.request().method() === "PUT") puts.push(route.request().postDataJSON());
    const day = new URL(route.request().url()).searchParams.get("day") ?? DAY;
    if (route.request().method() === "GET" && blockNextRead) {
      blockNextRead = false;
      started();
      await blocked;
    }
    await route.fulfill({ json: { day, doses: [dose(0, "Утро")] } });
  });
  await page.goto("/patient/medications");
  const morning = page.getByRole("checkbox", { name: /утром/ });
  try {
    await expect(morning).not.toBeChecked();
    blockNextRead = true;
    await morning.check();
    await refreshing;
    await page.clock.setFixedTime(new Date("2026-10-19T00:01:00+03:00"));
    release();

    await expect(
      page.getByText("Не удалось отправить. Проверьте связь и попробуйте ещё раз."),
    ).toBeVisible();
    await expect(morning).not.toBeChecked();
    expect(puts).toEqual([]);
  } finally {
    release();
  }
});

test("waking after midnight cannot apply a yesterday checkbox action to today", async ({
  page,
}) => {
  await mockSessionApi(page.context(), "patient");
  const beforeSleep = new Date(`${DAY}T23:59:00+03:00`);
  await page.clock.install({ time: beforeSleep });
  let day = DAY;
  let todayStatus: "taken" | null = null;
  const puts: Schemas["MarksIn"][] = [];
  await page.context().route("**/api/v1/me/doses**", async (route) => {
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON() as Schemas["MarksIn"];
      puts.push(body);
      todayStatus = body.marks[0]?.status === "taken" ? "taken" : null;
    }
    await route.fulfill({
      json: {
        day,
        doses: [{ ...dose(0, "Утро"), status: day === DAY ? "taken" : todayStatus }],
      },
    });
  });
  await page.goto("/patient/medications");
  const morning = page.getByRole("checkbox", { name: /утром/ });
  await expect(morning).toBeChecked();
  day = "2026-10-19";
  // Сон устройства меняет часы, но не исполняет интервал обновления экрана.
  await page.clock.setSystemTime(new Date("2026-10-19T08:01:00+03:00"));
  await expect(morning).toBeChecked();
  await morning.uncheck();

  await expect(page.getByRole("button", { name: "На главную" })).toBeEnabled();
  await expect(morning).not.toBeChecked();
  expect(puts).toEqual([]);
  // Новое нажатие уже относится к показанному сегодняшнему дню.
  await morning.check();
  await expect.poll(() => puts.length).toBe(1);
  expect(puts[0]).toEqual({
    marks: [{ prescription_id: PRESCRIPTION, day: "2026-10-19", slot: 0, status: "taken" }],
  });
});
