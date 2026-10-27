import type { Schemas } from "@compass/api-client";
import { expect, type Page, test } from "@playwright/test";
import { mockAssessmentApi } from "./assessment-api-mock";
import { SCALE_ID } from "./assessment-fixtures";
import { contrastRatio } from "./contrast";

// Спека #57: экран помощи после опроса. Синтетическое правило: «I felt calm.» = «Often» (5).
const HELP: Schemas["PatientHelpOut"] = {
  version: 1,
  checked_on: "2026-10-25",
  title: "Если сейчас очень тяжело",
  text: "Позвоните по номеру 112. Это бесплатно и круглосуточно.",
  sources: [
    {
      source: "synthetic-law",
      locator: "п. 2",
      title: "Синтетический документ о номере 112",
      url: "https://example.invalid/law",
    },
  ],
  contacts: [
    { id: "emergency", number: "112", label: "Позвонить 112", primary: true, sources: [] },
    { id: "ambulance", number: "103", label: "Скорая помощь 103", primary: false, sources: [] },
  ],
  triggers: [
    {
      rule_id: "synthetic-alert",
      rule_version: 1,
      when: { any: [{ scale: SCALE_ID, item: "calm", op: ">=", value: 5 }] },
    },
  ],
};
const SENT = "Мы сообщили вашему врачу.";
const QUEUED = "Сообщим врачу, как только появится связь.";

async function answer(page: Page, calm: "Never" | "Often"): Promise<void> {
  await page.goto(`/patient/assessments/${SCALE_ID}`);
  await page
    .getByRole("group", { name: "I felt calm." })
    .getByRole("radio", { name: calm })
    .check();
  await page
    .getByRole("group", { name: "I could rest." })
    .getByRole("radio", { name: "Often" })
    .check();
  // Данные экрана сохранены при открытии формы: дальше экран работает и без сети.
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem("compass.patient-help") !== null))
    .toBe(true);
}

function helpDialog(page: Page) {
  return page.getByRole("dialog", { name: "Если сейчас очень тяжело" });
}

test("a critical answer opens help at once and then reports the doctor alert", async ({
  page,
}, testInfo) => {
  const api = await mockAssessmentApi(page.context(), {
    patientHelp: HELP,
    alertRaised: (payload) => payload.answers.calm === 5,
  });
  await page.setViewportSize({ width: 360, height: 780 });
  await answer(page, "Often");
  api.holdNextSubmissionResponse();

  await page.getByRole("button", { name: "Отправить ответы" }).click();

  // До ответа сервера экран уже открыт, но о враче ещё ничего не сказано.
  const dialog = helpDialog(page);
  await expect(dialog).toBeVisible();
  await expect(dialog).not.toContainText(SENT);
  await expect(dialog.getByRole("link", { name: "Позвонить 112" })).toBeFocused();
  // Ответ сервера отпускаем, только когда запрос уже задержан: экран открылся раньше него.
  await expect.poll(() => api.heldSubmissionResponses()).toBe(1);
  await expect(dialog).not.toContainText(SENT);
  api.releaseHeldSubmissionResponse();
  await expect(dialog).toContainText(`${HELP.text} ${SENT}`);

  await expect(dialog.getByRole("link", { name: "Позвонить 112" })).toHaveAttribute(
    "href",
    "tel:112",
  );
  await expect(dialog.getByRole("link", { name: "Скорая помощь 103" })).toHaveAttribute(
    "href",
    "tel:103",
  );
  await dialog.getByText("Источники").click();
  await expect(dialog).toContainText("Синтетический документ о номере 112, п. 2");
  await expect(dialog).toContainText("Проверено 25.10.2026");
  const call = await dialog.getByRole("link", { name: "Позвонить 112" }).evaluate((element) => {
    const style = getComputedStyle(element);
    return { color: style.color, background: style.backgroundColor };
  });
  expect(contrastRatio(call.color, call.background)).toBeGreaterThanOrEqual(4.5);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await page.screenshot({ path: testInfo.outputPath("patient-help-360.png") });

  await dialog.getByRole("button", { name: "Вернуться к опросу" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByText("Результат сохранён на сервере")).toBeVisible();
});

test("offline the screen promises to tell the doctor once the network returns", async ({
  page,
}) => {
  const api = await mockAssessmentApi(page.context(), { patientHelp: HELP });
  await answer(page, "Often");
  api.setNetworkAvailable(false);

  await page.getByRole("button", { name: "Отправить ответы" }).click();

  await expect(helpDialog(page)).toContainText(QUEUED);
  await expect(helpDialog(page)).not.toContainText(SENT);
  expect(api.acceptedSubmissions).toHaveLength(0);
});

test("an ordinary answer does not open the screen", async ({ page }) => {
  await mockAssessmentApi(page.context(), { patientHelp: HELP });
  await answer(page, "Never");

  await page.getByRole("button", { name: "Отправить ответы" }).click();

  await expect(page.getByText("Результат сохранён на сервере")).toBeVisible();
  await expect(helpDialog(page)).toBeHidden();
});

test("a rejected answer keeps the numbers without a doctor notice", async ({ page }) => {
  const api = await mockAssessmentApi(page.context(), { patientHelp: HELP });
  await answer(page, "Often");
  api.setNextSubmissionResponse(409, "scale_version_changed");

  await page.getByRole("button", { name: "Отправить ответы" }).click();

  const dialog = helpDialog(page);
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("link", { name: "Позвонить 112" })).toBeVisible();
  // Дождаться ответа сервера: фраза не появится ни до, ни после.
  await expect.poll(() => api.submissions.length).toBe(1);
  await expect(dialog).not.toContainText(SENT);
  await expect(dialog).not.toContainText(QUEUED);
});

test("without stored rules the server alert still opens the fallback screen", async ({ page }) => {
  // Данных экрана нет (404): экран открывает ответ сервера, номер — запасной 112.
  await mockAssessmentApi(page.context(), { alertRaised: () => true });
  await page.goto(`/patient/assessments/${SCALE_ID}`);
  await page
    .getByRole("group", { name: "I felt calm." })
    .getByRole("radio", { name: "Never" })
    .check();
  await page
    .getByRole("group", { name: "I could rest." })
    .getByRole("radio", { name: "Often" })
    .check();

  await page.getByRole("button", { name: "Отправить ответы" }).click();

  const dialog = helpDialog(page);
  await expect(dialog).toContainText(SENT);
  await expect(dialog.getByRole("link", { name: "Позвонить 112" })).toHaveAttribute(
    "href",
    "tel:112",
  );
  await expect(dialog.getByRole("link", { name: "Скорая помощь 103" })).toHaveCount(0);
});

test("the home link opens help without a doctor notice and goes back", async ({ page }) => {
  await mockAssessmentApi(page.context(), { patientHelp: HELP });
  await page.setViewportSize({ width: 360, height: 780 });
  await page.goto("/patient/");

  await page.getByRole("link", { name: "Если очень тяжело" }).click();

  const dialog = helpDialog(page);
  await expect(dialog).toContainText(HELP.text);
  await expect(dialog).not.toContainText(SENT);
  await expect(dialog).not.toContainText(QUEUED);
  await dialog.getByRole("button", { name: "Назад" }).click();
  await expect(page).toHaveURL(/\/patient\/$/);
  await expect(page.getByRole("heading", { name: "Как вы сейчас?" })).toBeVisible();
});
