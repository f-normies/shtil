import type { Schemas } from "@compass/api-client";
import { type BrowserContext, expect, type Page, test } from "@playwright/test";
import { mockSessionApi } from "./session-api-mock";

const PRESCRIPTION = "00000000-0000-4000-8000-0000000000e1";
const REACTIONS = `/api/v1/me/prescriptions/${PRESCRIPTION}/reactions`;

const ITEM: Schemas["ScheduleItem"] = {
  prescription_id: PRESCRIPTION,
  trade_name: "Синтетин",
  inn: "синтетин",
  dose_mg: 20,
  times_per_day: 2,
  schedule: "утром и вечером",
  started_at: "2026-10-11T09:00:00Z",
  ends_on: "2026-11-08",
  instructions: ["Принимать независимо от приёма пищи"],
  reactions: [
    { id: "headache", title: "болит голова" },
    { id: "hyperhidrosis", title: "сильная потливость" },
    { id: "muscle-stiffness", title: "скованность мышц" },
  ],
  appearance: null,
  appearance_origin: "unknown",
  appearance_revision: 0,
  default_appearance: null,
};

/** Схема приёма и отметка реакции; ответ на отметку задаёт тест. */
async function mockTherapy(context: BrowserContext, reportStatus: number) {
  const reports: unknown[] = [];
  await context.route("**/api/v1/me/prescriptions**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/v1/me/prescriptions" && request.method() === "GET") {
      await route.fulfill({ status: 200, json: [ITEM] });
      return;
    }
    if (path === REACTIONS && request.method() === "POST") {
      reports.push(request.postDataJSON());
      await route.fulfill(
        reportStatus === 201
          ? { status: 201, json: { id: "r1", prescription_id: PRESCRIPTION, reactions: [] } }
          : { status: reportStatus, json: { status: reportStatus, title: "Conflict" } },
      );
      return;
    }
    await route.fallback();
  });
  return reports;
}

async function openTherapy(page: Page): Promise<void> {
  await page.setViewportSize({ width: 360, height: 780 });
  await page.goto("/patient/");
  await page.getByRole("link", { name: "Открыть схему приёма" }).click();
  await expect(page.getByRole("heading", { name: "Моя терапия" })).toBeVisible();
}

test("patient reports reactions from the label list at 360 px", async ({ page }) => {
  await mockSessionApi(page.context(), "patient");
  const reports = await mockTherapy(page.context(), 201);
  await openTherapy(page);

  await expect(page.getByText("по 20 мг 2 раза в сутки, утром и вечером")).toBeVisible();
  await expect(page.getByText("по 7 ноября 2026 г. включительно", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Сообщить о нежелательной реакции" }).click();
  const send = page.getByRole("button", { name: "Отправить врачу" });
  await expect(send).toBeDisabled();
  await page.getByLabel("скованность мышц").check();
  await page.getByLabel("сильная потливость").check();
  await page.getByLabel("Что ещё хотите сообщить (необязательно)").fill("  с утра сводит челюсть ");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await send.click();

  await expect(page.getByText("Сообщение отправлено лечащему врачу.")).toBeVisible();
  // Порядок — как в перечне инструкции, а не как отмечал пациент.
  expect(reports).toEqual([
    { reactions: ["hyperhidrosis", "muscle-stiffness"], note: "с утра сводит челюсть" },
  ]);
});

test("stopped prescription explains why the report was not accepted", async ({ page }) => {
  await mockSessionApi(page.context(), "patient");
  await mockTherapy(page.context(), 409);
  await openTherapy(page);

  await page.getByRole("button", { name: "Сообщить о нежелательной реакции" }).click();
  await page.getByLabel("болит голова").check();
  await page.getByRole("button", { name: "Отправить врачу" }).click();

  await expect(page.getByRole("alert")).toContainText(
    "Назначение уже не действует. Обновите страницу.",
  );
});
