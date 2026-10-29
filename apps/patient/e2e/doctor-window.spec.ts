import { expect, test } from "@playwright/test";
import { ALERTS, KOTOV, LEBEDEVA, ORLOVA, openWindow, SCHEDULE } from "./doctor-window-mock";

/** Окно кабинета врача v4 (спека #13, раздел 10; #99): «Пациенты» и «Обзор» карточки. Данные — `doctor-window-mock.ts`. */

test("patients: critical alerts failure blocks the queue until retry succeeds", async ({
  page,
}) => {
  let unavailable = true;
  await page.route(/\/api\/v1\/alerts(?:\?|$)/, (route) =>
    route.fulfill({
      status: unavailable ? 503 : 200,
      json: unavailable ? { detail: "Temporarily unavailable" } : ALERTS,
    }),
  );
  await openWindow(page);
  const warning = page.getByRole("alert").filter({ hasText: "Очередь пациентов недоступна" });
  await expect(warning).toContainText("Не удалось загрузить сигналы");
  await expect(page.getByRole("listbox", { name: "Пациенты" })).toHaveCount(0);
  await expect(page.getByText("Остальные", { exact: true })).toHaveCount(0);

  unavailable = false;
  await warning.getByRole("button", { name: "Повторить" }).click();
  const attention = page
    .getByRole("listbox", { name: "Пациенты" })
    .getByRole("group", { name: "Требуют внимания" });
  await expect(attention.getByRole("option", { name: /Лебедева/ })).toBeVisible();
  await expect(attention.getByRole("option", { name: /Котов/ })).toBeVisible();
  await expect(warning).toHaveCount(0);
});

for (const path of ["/doctor/", `/doctor/patients/${LEBEDEVA}`]) {
  test(`compact scale schedule failure warns and retry restores gaps: ${path}`, async ({
    page,
  }) => {
    let unavailable = true;
    await page.route(`**/api/v1/patients/${LEBEDEVA}/assessments/schedule`, (route) =>
      route.fulfill({
        status: unavailable ? 503 : 200,
        json: unavailable ? { detail: "Temporarily unavailable" } : SCHEDULE,
      }),
    );
    await openWindow(page, path);
    const state = page.getByRole("region", { name: "Состояние", exact: true });
    const warning = state.getByText("Пропуски не показаны: не удалось загрузить расписание шкал");
    await expect(warning).toBeVisible();
    await expect(state.getByText("PHQ-9", { exact: true })).toBeVisible();

    unavailable = false;
    await warning.getByRole("button", { name: "Повторить" }).click();
    await expect(warning).toHaveCount(0);
    const phq = state.getByRole("listitem").filter({ hasText: "PHQ-9" });
    await expect(phq.locator("svg line[stroke-dasharray]")).toHaveCount(1);
  });
}

test("patients: attention first by level, then proposals; snoozed stays with the rest", async ({
  page,
}, testInfo) => {
  await openWindow(page);
  const list = page.getByRole("listbox", { name: "Пациенты" });
  const attention = list.getByRole("group", { name: "Требуют внимания" });
  await expect(attention.getByRole("option")).toHaveText([/Лебедева/, /Котов/, /Ершов/]);
  await expect(attention.getByRole("option", { name: /Ершов/ })).toContainText(
    "2 предложения ждут решения",
  );
  const rest = list.getByRole("group", { name: "Остальные" });
  await expect(rest.getByRole("option")).toHaveText([/Орлова/, /Фролов/]);
  await expect(rest.getByRole("option", { name: /Орлова/ })).toContainText("PHQ-9 7, GAD-7 8");

  // Без `?patient=` выбран первый в очереди; подробности — справа без перехода.
  await expect(list.getByRole("option", { name: /Лебедева/ })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page.getByRole("heading", { name: "Лебедева Марина Викторовна" })).toBeVisible();
  await expect(page.getByText("Лёгкая депрессия, без изменений 2 недели")).toBeVisible();
  await expect(page.getByText("Умеренная тревога → Лёгкая тревога с 19 окт")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("02-patients.png") });
});

test("patients: arrows move the selection, Enter opens the card, back keeps the row", async ({
  page,
}) => {
  await openWindow(page);
  const list = page.getByRole("listbox", { name: "Пациенты" });
  await list.focus();
  await page.keyboard.press("ArrowDown");
  await expect(page).toHaveURL(new RegExp(`patient=${KOTOV}`));
  await expect(page.getByRole("heading", { name: "Котов Сергей Андреевич" })).toBeVisible();
  // Сигнал в работе: решение фиксируют на экране «Сигналы».
  await expect(page.getByRole("link", { name: "Зафиксировать решение" })).toBeVisible();

  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`/doctor/patients/${KOTOV}$`));
  await page.getByRole("link", { name: "Пациенты", exact: true }).click();
  await expect(list.getByRole("option", { name: /Котов/ })).toHaveAttribute(
    "aria-selected",
    "true",
  );
});

test("search finds a patient by part of the name ignoring case", async ({ page }) => {
  await openWindow(page);
  await page.getByPlaceholder("Поиск по ФИО").fill("ОРЛОВ");
  await expect(page.getByRole("option")).toHaveText([/Орлова/]);
  await page.getByPlaceholder("Поиск по ФИО").fill("нет такого");
  await expect(page.getByText("Никого не нашли по «нет такого»")).toBeVisible();
  await page.getByRole("button", { name: "Сбросить поиск" }).click();
  await expect(page.getByRole("option")).toHaveCount(5);
});

test("menu counts patients, open signals in the mandatory tone and pending proposals", async ({
  page,
}) => {
  await openWindow(page);
  const menu = page.getByRole("navigation", { name: "Разделы кабинета" });
  await expect(menu.getByRole("link", { name: /Пациенты/ })).toContainText("5");
  await expect(menu.getByRole("link", { name: /Сигналы/ })).toContainText("3");
  // Уровень счётчика не только цветом: значок со словом.
  await expect(menu.getByRole("link", { name: /Сигналы/ })).toHaveAccessibleName(
    /есть «Обязательно»/,
  );
  await expect(menu.getByRole("link", { name: /Предложения/ })).toContainText("3");
});

test("overview: decision group with the rule source, state, proposals and feed by day", async ({
  page,
}, testInfo) => {
  await openWindow(page, `/doctor/patients/${LEBEDEVA}`);
  await expect(page.getByText("34 года, F41.1, наблюдение с 22.09")).toBeVisible();
  const decide = page.getByRole("region", { name: "Требует решения" });
  await expect(
    decide.getByRole("heading", { name: "Мысли о смерти или самоповреждении" }),
  ).toBeVisible();
  await expect(
    decide.getByText("Правило «Мысли о смерти или самоповреждении», версия 1"),
  ).toBeVisible();
  await expect(decide.getByRole("button", { name: "Взять в работу" })).toBeVisible();
  await expect(
    decide.getByText(/^PHQ-9, пункт 9: ответ «Несколько дней», 1 балл\. Сигнал 19\.10/),
  ).toBeVisible();
  await expect(page.getByRole("region", { name: "Предложения системы" })).toContainText(
    "ждут решения: 1",
  );
  await expect(page.getByRole("region", { name: "Сегодня, 19 октября" })).toBeVisible();
  // На обзоре пациента его сигнал уже в «Требует решения»: плашка его не повторяет.
  await expect(page.getByRole("alert", { name: "Сигналы «Обязательно»" })).toBeHidden();
  await expect(page.getByRole("radio", { name: "Обзор" })).toBeChecked();
  await expect(page.getByRole("radio", { name: "Дневник" })).toBeDisabled();
  // Панели прокручиваются сами: для снимка всего обзора окно выше.
  await page.setViewportSize({ width: 1280, height: 1600 });
  await page.screenshot({ path: testInfo.outputPath("05-overview.png") });
});

test("mandatory banner shows on other screens and leads to the signal", async ({ page }) => {
  await openWindow(page, `/doctor/patients/${KOTOV}`);
  const banner = page.getByRole("alert", { name: "Сигналы «Обязательно»" });
  await expect(banner).toContainText("Лебедева Марина Викторовна — Мысли о смерти");
  await banner.getByRole("link", { name: "Открыть сигнал" }).click();
  await expect(page).toHaveURL(/\/doctor\/alerts\/mandatory/);
});

test("the banner does not push the bottom of the list off the screen", async ({ page }) => {
  await openWindow(page);
  await expect(page.getByRole("alert", { name: "Сигналы «Обязательно»" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Пригласить пациента" })).toBeInViewport();
});

test("card without open signals still links to the signal history", async ({ page }) => {
  await openWindow(page, `/doctor/patients/${ORLOVA}`);
  await expect(page.getByRole("region", { name: "Требует решения" })).toBeHidden();
  await page.getByRole("link", { name: "Все сигналы (1)" }).click();
  await expect(page).toHaveURL(new RegExp(`/doctor/alerts\\?patient=${ORLOVA}`));
});

test("«Зафиксировать решение» lands on the decision block of the signal", async ({ page }) => {
  await openWindow(page, `/doctor/?patient=${KOTOV}`);
  await page.getByRole("link", { name: "Зафиксировать решение" }).click();
  await expect(page).toHaveURL(/\/doctor\/alerts\/desirable#decision/);
  const block = page.getByRole("region", { name: "Зафиксировать решение" });
  await expect(block).toBeInViewport();
  await expect(block.locator("..")).toBeFocused();
});

test("scales: levels, a gap from the schedule and a popover with item answers", async ({
  page,
}, testInfo) => {
  await openWindow(page, `/doctor/patients/${LEBEDEVA}/scales`);
  const gad = page.getByRole("group", { name: "GAD-7, 3 измерения" });
  const phq = page.getByRole("group", { name: "PHQ-9, 2 измерения" });
  await expect(gad).toBeVisible();
  // Градация через пропуск не менялась: пропуск не выдаётся за изменение.
  await expect(
    page.getByText("Лёгкая депрессия, без изменений 2 недели", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Таблицей" })).toHaveCount(0);

  // Tab — одна остановка: фокус открывает последнюю точку, стрелки идут по точкам.
  await gad.focus();
  await expect(gad).toContainText("GAD-7: 9 из 21");
  await expect(gad).toContainText("−3 балла к 12 окт, Умеренная тревога → Лёгкая тревога");
  await expect(gad).toContainText("Ответы по пунктам");
  await page.waitForTimeout(300); // поповер проявляется 150 мс
  await page.setViewportSize({ width: 1280, height: 1200 });
  await page.screenshot({ path: testInfo.outputPath("09-scales.png"), fullPage: true });
  await page.keyboard.press("Escape");
  await expect(gad).not.toContainText("Ответы по пунктам");

  // Пропуск — та же цель для клавиатуры, поповер «Отметки не было».
  await phq.focus();
  await page.keyboard.press("ArrowLeft");
  await expect(phq).toContainText("Отметки не было");
  await expect(phq).toContainText("12 октября, понедельник, отметки не было");

  // Опросы пациента (#180): частота каждой шкалы и кто её изменил.
  const surveys = page.getByRole("region", { name: "Опросы пациента" });
  await expect(surveys).toContainText("Изменено: ");
  await expect(surveys.getByRole("checkbox", { name: "Опрашивать PHQ-9" })).toBeChecked();
  await expect(
    surveys.getByRole("radiogroup", { name: "Частота PHQ-9" }).getByLabel("Раз в неделю"),
  ).toBeChecked();
  await expect(
    surveys.getByRole("radiogroup", { name: "Частота GAD-7" }).getByLabel("Раз в две недели"),
  ).toBeChecked();
  const saved = page.waitForRequest(
    (r) => r.method() === "PUT" && r.url().endsWith("/assessments/settings/gad-7"),
  );
  await surveys
    .getByRole("radiogroup", { name: "Частота GAD-7" })
    .getByLabel("Раз в неделю")
    .click();
  expect((await saved).postDataJSON()).toEqual({ enabled: true, period_days: 7 });

  const clinician = page.getByRole("region", { name: "Шкалы врача" });
  await expect(clinician).toContainText("HAMD-17");
  await expect(clinician).toContainText("шкала 0–52, ещё не заполняли");
  // Ввод шкалы врачом (#94): у каждой шкалы врача кнопка «Заполнить».
  await expect(clinician.getByRole("button", { name: "Заполнить HAMD-17" })).toBeVisible();
});

test("the old single-scale address leads to the scales section", async ({ page }) => {
  await openWindow(page, `/doctor/patients/${LEBEDEVA}/scales/phq-9`);
  await expect(page).toHaveURL(new RegExp(`/doctor/patients/${LEBEDEVA}/scales$`));
  await expect(page.getByRole("radio", { name: "Шкалы" })).toBeChecked();
});
