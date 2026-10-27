import { expect, test } from "@playwright/test";
import { ORLOVA, openTherapy } from "./doctor-therapy-mock";

/** «Терапия» по кадру «Врач v4 · 10» (спека #28, раздел 8). Данные — `doctor-therapy-mock.ts`. */

test("open proposal shows the schedule, the course bar and checks with words", async ({
  page,
}, testInfo) => {
  await openTherapy(page);
  const offer = page.getByRole("region", { name: "Предложение назначения" });
  await expect(offer).toContainText("Предложение системы");
  await expect(offer).toContainText(
    "Показание по инструкции: тревожное состояние после COVID-19, F43.2, F43.8",
  );
  await expect(offer.getByRole("listitem").filter({ hasText: "Утро" })).toContainText(
    "20 мг, 1 таблетка",
  );
  await expect(offer.getByRole("listitem").filter({ hasText: "Вечер" })).toContainText(
    "20 мг, 1 таблетка",
  );
  await expect(
    offer.getByRole("img", {
      name: "Курс 4 недели, до 16 ноября, при необходимости до 30 ноября",
    }),
  ).toBeVisible();
  const checks = page.getByRole("region", { name: "Проверки по инструкции" });
  await expect(checks).toContainText("4 пройдены, 1 проверьте сами");
  await expect(checks.getByRole("option", { name: /Возраст от 18 лет/ })).toContainText("Пройдена");
  await expect(checks.getByRole("option", { name: /степень выраженности/ })).toContainText(
    "Проверьте сами",
  );

  // Клик по строке выбирает её и показывает источник проверки.
  await checks.getByRole("option", { name: /Показание/ }).click();
  await expect(checks.getByRole("option", { name: /Показание/ })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(checks.getByRole("button", { name: /ОХЛП «Авиандр».*4\.1/ })).toBeVisible();
  await page.setViewportSize({ width: 1280, height: 1800 });
  await page.locator("main").evaluate((main) => main.scrollTo(0, 0));
  await page.screenshot({ path: testInfo.outputPath("10-therapy.png") });
});

test("doctor confirms the proposal with one button", async ({ page }) => {
  const posted = await openTherapy(page);
  await page.getByRole("button", { name: "Подтвердить назначение" }).click();
  await expect.poll(() => posted).toContain("/api/v1/proposals/proposal-1/confirm");
});

test("off-label schedule needs a reason in the sheet", async ({ page }) => {
  const posted = await openTherapy(page);
  await page.getByRole("button", { name: "Изменить схему" }).click();
  const sheet = page.getByRole("dialog", { name: "Изменить схему" });
  await sheet.getByLabel("Разовая доза, мг").fill("40");
  await expect(sheet.getByText(/Вне инструкции/)).toBeVisible();
  const save = sheet.getByRole("button", { name: "Назначить по этой схеме" });
  await expect(save).toBeDisabled();
  await sheet.getByLabel("Обоснование, обязательно").fill("Неполный ответ на стандартную дозу");
  await save.click();
  await expect.poll(() => posted).toContain("/api/v1/therapy/proposals/proposal-1/revise");
  await expect(sheet).toBeHidden();
});

test("after matching and confirming, the proposal no longer waits for the doctor", async ({
  page,
}) => {
  const posted = await openTherapy(page);
  await page.getByRole("button", { name: "Подобрать терапию" }).click();
  await expect.poll(() => posted).toContain(`/api/v1/patients/${ORLOVA}/therapy/options`);
  await page.getByRole("button", { name: "Подтвердить назначение" }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "Назначение подтверждено" }),
  ).toBeFocused();
  await expect(page.getByRole("region", { name: "Предложение назначения" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Подтвердить назначение" })).toHaveCount(0);
});

test("rejecting needs a reason", async ({ page }) => {
  const posted = await openTherapy(page);
  await page.getByRole("button", { name: "Отклонить" }).click();
  const reason = page.getByLabel("Причина отклонения, обязательно");
  await expect(reason).toBeFocused();
  const send = page.getByRole("button", { name: "Отклонить предложение" });
  await expect(send).toBeDisabled();
  await reason.fill("Пациентка предпочитает психотерапию");
  await send.click();
  await expect.poll(() => posted).toContain("/api/v1/proposals/proposal-1/reject");
  await expect(page.getByRole("status").filter({ hasText: "Предложение отклонено" })).toBeVisible();
});
