import type { Schemas } from "@compass/api-client";
import { expect, type Page, test } from "@playwright/test";
import { mockAssessmentApi } from "./assessment-api-mock";
import { SCALE_ID } from "./assessment-fixtures";
import { initial, mockQueue } from "./doctor-proposals-mock";
import { alert, openSignals, KOTOV as SIGNALS_KOTOV } from "./doctor-signals-mock";
import { openTherapy } from "./doctor-therapy-mock";
import { KOTOV, LEBEDEVA, openWindow } from "./doctor-window-mock";
import { mockSessionApi } from "./session-api-mock";
import { auditScreen } from "./signal-audit";
import { documentsWithFacts } from "./signal-visibility-documents";
import { therapyRisks } from "./signal-visibility-therapy";

/**
 * #36: сигналы, предложения системы и предупреждения различимы не только цветом на всех
 * готовых экранах. Правила — спека `2026-10-24-36-signal-visibility-design.md`.
 * Имя снимка — номер и название кадра Figma. Данные выдуманы.
 */

const doctor = { cabinet: "doctor" } as const;

async function settled(page: Page) {
  // Отменённая анимация отклоняет `finished` с AbortError: её конец ждать не нужно.
  await page.evaluate(() =>
    Promise.all(document.getAnimations().map((a) => a.finished.catch(() => undefined))),
  );
}

test("02 patients and the menu counter", async ({ page }, testInfo) => {
  await openWindow(page);
  const menu = page.getByRole("navigation", { name: "Разделы кабинета" });
  await expect(menu.getByRole("link", { name: /Сигналы/ })).toHaveAccessibleName(
    /есть «Обязательно»/,
  );
  await auditScreen(page, testInfo, { ...doctor, name: "02-patsienty" });
});

test("05 card overview with a mandatory signal and a pending proposal", async ({
  page,
}, testInfo) => {
  await openWindow(page, `/doctor/patients/${LEBEDEVA}`);
  await expect(page.getByRole("region", { name: "Требует решения" })).toBeVisible();
  await page.setViewportSize({ width: 1280, height: 1600 });
  await auditScreen(page, testInfo, { ...doctor, name: "05-kartochka-obzor" });
});

test("12 mandatory banner without a connection", async ({ page }, testInfo) => {
  // Подмена сессии закрывает WebSocket сразу: кабинет показывает строку потери связи.
  await openWindow(page, `/doctor/patients/${KOTOV}`);
  await expect(page.getByRole("alert", { name: "Сигналы «Обязательно»" })).toBeVisible();
  await expect(page.getByText(/^Нет связи с сервером/)).toBeVisible();
  await auditScreen(page, testInfo, { ...doctor, name: "12-plashka-obyazatelno-net-svyazi" });
});

test("03 mandatory signal", async ({ page }, testInfo) => {
  await openSignals(page);
  await page.getByRole("link", { name: /Лебедева/ }).click();
  await expect(page.getByText("Порог: от 1", { exact: true })).toBeVisible();
  await auditScreen(page, testInfo, { ...doctor, name: "03-signaly" });
});

test("03b desirable signal", async ({ page }, testInfo) => {
  await openSignals(page);
  await page.getByRole("link", { name: /Котов/ }).click();
  await expect(page.getByText("Желательно от 10")).toBeVisible();
  await auditScreen(page, testInfo, { ...doctor, name: "03b-signaly-zhelatelnyy" });
});

test("11 referral sheet and decline with a reason", async ({ page }, testInfo) => {
  await openSignals(page);
  await page.getByRole("link", { name: /Котов/ }).click();
  await page.getByRole("button", { name: "Направить к психиатру" }).click();
  const sheet = page.getByRole("dialog", { name: "Направление к психиатру" });
  await expect(sheet.getByText("Желательно от 10")).toBeVisible();
  await settled(page);
  await auditScreen(page, testInfo, { ...doctor, name: "11-napravlenie-k-psikhiatru" });

  await sheet.getByRole("button", { name: "Отклонить с причиной" }).click();
  await sheet.getByLabel("Пациент отказался от консультации").check();
  await settled(page);
  await auditScreen(page, testInfo, { ...doctor, name: "11v-napravlenie-otklonit-s-prichinoy" });
});

/** Маршрутизация «Обязательно» у Котова: тяжёлая депрессия по сумме PHQ-9 (кадр 11г). */
const SEVERE = alert({
  id: "severe",
  patient_id: SIGNALS_KOTOV,
  rule_id: "routing-depression-severe",
  kind: "routing",
  title: "Тяжёлая депрессивная симптоматика",
  last_triggered_at: "2026-10-22T07:00:00Z",
});

test("11g mandatory referral to a psychiatrist", async ({ page }, testInfo) => {
  await openSignals(page, [SEVERE]);
  await page.getByRole("link", { name: /Котов.*Тяжёлая/ }).click();
  await page.getByRole("button", { name: "Направить к психиатру" }).click();
  const sheet = page.getByRole("dialog", { name: "Направление к психиатру" });
  await expect(sheet.getByText("Обязательно", { exact: false }).first()).toBeVisible();
  await settled(page);
  await auditScreen(page, testInfo, { ...doctor, name: "11g-napravlenie-obyazatelno" });
});

test("11b snooze a desirable signal", async ({ page }, testInfo) => {
  await openSignals(page);
  await page.getByRole("link", { name: /Котов/ }).click();
  await page.getByRole("button", { name: "Отложить до…" }).click();
  await expect(page.getByRole("heading", { name: "Отложить сигнал до" })).toBeVisible();
  await settled(page);
  await auditScreen(page, testInfo, { ...doctor, name: "11b-napravlenie-otlozhit-do" });
});

test("03 signals in work and resolved", async ({ page }, testInfo) => {
  const inWork = alert({
    id: "work",
    patient_id: SIGNALS_KOTOV,
    level: "desirable",
    kind: "routing",
    rule_id: "routing-depression-probable",
    title: "Вероятная депрессия",
    state: "in_work",
    acknowledged_at: "2026-10-22T08:00:00Z",
  });
  const resolved = alert({
    id: "done",
    state: "resolved",
    resolved_at: "2026-10-21T10:00:00Z",
    title: "Мысли о смерти или самоповреждении",
  });
  await openSignals(page, [inWork], async (context) => {
    await context.route(/\/api\/v1\/alerts\?.*status=resolved/, (route) =>
      route.fulfill({ json: [resolved] }),
    );
  });
  await expect(page.getByRole("region", { name: "В работе у вас" })).toBeVisible();
  await auditScreen(page, testInfo, { ...doctor, name: "03-signaly-v-rabote" });

  await page.getByRole("link", { name: "Решённые" }).click();
  await expect(page.getByRole("region", { name: "Решённые" })).toBeVisible();
  await auditScreen(page, testInfo, { ...doctor, name: "03-signaly-reshennye" });
});

test("04 proposals: pending, confirmed and rejected", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 952 });
  await mockSessionApi(page.context(), "doctor");
  await mockQueue(page.context(), initial());
  await page.goto("/doctor/proposals");
  const details = page.getByRole("article");
  await expect(details.getByText("Предложение системы").first()).toBeVisible();
  await settled(page);
  await auditScreen(page, testInfo, { ...doctor, name: "04-predlozheniya" });

  await page.getByRole("radio", { name: "Подтверждены" }).check();
  await expect(details.getByText("Подтверждено врачом")).toBeVisible();
  await settled(page);
  await auditScreen(page, testInfo, { ...doctor, name: "04-predlozheniya-podtverzhdeno" });

  // Отклонённое — нейтральное: без пунктира и значков статуса, с причиной врача.
  await page.getByRole("radio", { name: "Отклонены" }).check();
  await expect(details.getByText("Причина: приняла позже, уточнила в чате")).toBeVisible();
  await settled(page);
  await expect(details.locator(".ui-status-badge")).toHaveCount(0);
  await auditScreen(page, testInfo, { ...doctor, name: "04-predlozheniya-otkloneno" });
});

test("10 therapy with a pending prescription proposal", async ({ page }, testInfo) => {
  await openTherapy(page);
  await expect(page.getByRole("heading", { name: /Авиандр/ }).first()).toBeVisible();
  await auditScreen(page, testInfo, { ...doctor, name: "10-kartochka-terapiya" });
});

test("10 therapy: safety checks, reactions, off-label prescription", async ({ page }, testInfo) => {
  await openTherapy(page, therapyRisks());
  await expect(page.getByText("Кожная сыпь — «сыпь»")).toBeVisible();
  await expect(page.getByText("Приём алкоголя")).toBeVisible();
  await page.setViewportSize({ width: 1280, height: 2700 });
  await auditScreen(page, testInfo, { ...doctor, name: "10-kartochka-terapiya-riski" });
});

test("10 therapy: a contraindication blocks the prescription", async ({ page }, testInfo) => {
  await openTherapy(page, therapyRisks({ blocked: true }));
  await page.getByRole("button", { name: "Подобрать терапию" }).click();
  await expect(page.getByRole("region", { name: "Назначить нельзя" })).toBeVisible();
  await page.setViewportSize({ width: 1280, height: 1100 });
  await auditScreen(page, testInfo, { ...doctor, name: "10-kartochka-terapiya-nelzya" });
});

test("07b documents: facts wait for a decision, one confirmed, one rejected", async ({
  page,
}, testInfo) => {
  await openWindow(
    page,
    `/doctor/patients/${LEBEDEVA}/documents?document=doc-1`,
    documentsWithFacts,
  );
  await expect(page.getByText("Мелатонин").first()).toBeVisible();
  await page.setViewportSize({ width: 1280, height: 1600 });
  await settled(page);
  await auditScreen(page, testInfo, { ...doctor, name: "07b-kartochka-predlozheniya-dokument" });
});

const patient = { cabinet: "patient" } as const;

test("patient home and scales at 360 px have no red", async ({ page }, testInfo) => {
  await mockAssessmentApi(page.context());
  await page.setViewportSize({ width: 360, height: 780 });
  await page.goto("/patient/");
  await expect(page.getByRole("link", { name: "Открыть схему приёма" })).toBeVisible();
  await auditScreen(page, testInfo, { ...patient, name: "patient-glavnyy" });

  await page.goto(`/patient/assessments/${SCALE_ID}`);
  await expect(page.locator("fieldset legend").first()).toBeVisible();
  await auditScreen(page, testInfo, { ...patient, name: "patient-opros" });
});

const SCHEDULE: Schemas["ScheduleItem"] = {
  prescription_id: "00000000-0000-4000-8000-0000000000e1",
  trade_name: "Авиандр",
  inn: "маритупирдин",
  dose_mg: 20,
  times_per_day: 2,
  schedule: "утром и вечером",
  started_at: "2026-10-11T09:00:00Z",
  ends_on: "2026-11-08",
  instructions: ["Внутрь за 30 минут до еды"],
  appearance: null,
  appearance_origin: "unknown",
  appearance_revision: 0,
  default_appearance: null,
  reactions: [
    { id: "rash", title: "сыпь на коже" },
    { id: "dizziness", title: "кружится голова" },
  ],
};

test("patient therapy at 360 px has no red", async ({ page }, testInfo) => {
  await mockSessionApi(page.context(), "patient");
  await page
    .context()
    .route("**/api/v1/me/prescriptions", (route) => route.fulfill({ json: [SCHEDULE] }));
  await page.setViewportSize({ width: 360, height: 780 });
  await page.goto("/patient/therapy");
  await expect(page.getByRole("heading", { name: "Моя терапия" })).toBeVisible();
  await auditScreen(page, testInfo, { ...patient, name: "patient-terapiya" });
});
