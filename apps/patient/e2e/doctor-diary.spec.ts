import { expect, type Page, test, type WebSocketRoute } from "@playwright/test";
import {
  DIARY,
  type DiaryMock,
  diaryMock,
  lastAsked,
  MORNING_VOICE,
  mockDiary,
  shoot,
} from "./doctor-diary-mock";
import { LEBEDEVA, openWindow, patient } from "./doctor-window-mock";

/**
 * Вкладка «Дневник» карточки врача (спека #115; кадры 06–06д) на подменённых ручках #31.
 * Браузер — во Владивостоке: дни и время вкладка считает по поясу сервера, Москве.
 * Данные — `doctor-diary-mock.ts`.
 */

test.use({ timezoneId: "Asia/Vladivostok" });

/** 19.10, 23:30 по Москве — во Владивостоке уже 20.10, 06:30. */
const LATE_EVENING = new Date("2026-10-19T23:30:00+03:00");

async function openDiary(page: Page, mock: DiaryMock, path = `/doctor/patients/${LEBEDEVA}/diary`) {
  const sockets: WebSocketRoute[] = [];
  await openWindow(page, path, async (context) => {
    await page.clock.setFixedTime(LATE_EVENING);
    await mockDiary(context, mock);
    await page.routeWebSocket("**/api/v1/ws", (socket) => {
      sockets.push(socket);
      socket.send(JSON.stringify({ type: "ready" }));
    });
  });
  return sockets;
}

test("diary tab: two weeks by server days, period switch, voice record and its finding", async ({
  page,
}, testInfo) => {
  const mock = diaryMock();
  await page
    .context()
    .addCookies([
      { name: "compass_session", value: "synthetic-session", url: "http://127.0.0.1:5174" },
    ]);
  const sockets = await openDiary(page, mock, `/doctor/patients/${LEBEDEVA}`);

  await page.getByRole("radio", { name: "Дневник" }).click();
  await expect(page).toHaveURL(new RegExp(`/patients/${LEBEDEVA}/diary$`));
  await expect(page.getByRole("radio", { name: "Дневник" })).toBeChecked();

  // График за 2 недели по московским дням: у врача во Владивостоке уже 20.10, у сервера 19.10.
  await expect(page.getByText("Настроение, 6–19 октября")).toBeVisible();
  await expect(page.getByText("13 из 14 дней")).toBeVisible();
  await expect(
    page.getByText("За 14 дней: ровно 6, тяжело 5, неплохо 2, без отметки 1."),
  ).toBeVisible();
  await expect
    .poll(() => lastAsked(mock, `${DIARY}/moods`)?.toString())
    .toBe("start=2026-10-06&end=2026-10-19");
  const firstPage = lastAsked(mock, DIARY);
  expect(firstPage?.get("start")).toBe("2026-10-06");
  expect(firstPage?.get("end")).toBe("2026-10-19");
  expect(firstPage?.get("filter")).toBe("all");
  expect(firstPage?.has("cursor")).toBe(false);
  await expect(page.locator("table caption")).toHaveText("Отметки настроения, 6–19 октября");

  // Записи: голос с плеером и расшифровкой, статус «Расшифровывается…», отметки; время московское.
  await expect(page.getByText("Сегодня, 19 октября")).toBeVisible();
  await expect(
    page.getByText("Ночью опять просыпалась, потом долго не могла уснуть."),
  ).toBeVisible();
  await expect(page.getByText("Расшифровывается…")).toBeVisible();
  await expect(page.getByRole("button", { name: "Воспроизвести запись, 0:42" })).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Предложение системы: нарушение сна" }),
  ).toBeVisible();
  await expect(page.getByText("Симптом, голосовая запись 18.10, 21:30")).toBeVisible();
  await shoot(page, testInfo, "06-diary-2-weeks.png");

  // Расшифровка готова: сервер шлёт `diary.changed`, вкладка перечитывает записи без опроса.
  mock.transcribed = true;
  sockets.at(-1)?.send(
    JSON.stringify({
      type: "diary.changed",
      data: { patient_id: LEBEDEVA, entry_id: "voice-19", audio_status: "done" },
    }),
  );
  await expect(page.getByText(MORNING_VOICE)).toBeVisible();
  await expect(page.getByText("Расшифровывается…")).toHaveCount(0);

  // Голос — по сессии врача: `<audio>` идёт в ручку #31 с cookie того же сайта.
  const audioRequest = page.waitForRequest((r) => r.url().endsWith(`${DIARY}/voice-18/audio`));
  await page.getByRole("button", { name: "Воспроизвести запись, 0:42" }).click();
  const request = await audioRequest;
  expect(await request.headerValue("cookie")).toContain("compass_session=synthetic-session");

  // «Показать ещё записи» берёт следующую страницу по курсору.
  await page.getByRole("button", { name: "Показать ещё записи" }).click();
  await expect(page.getByText("Вечером уснула и не выпила таблетку.")).toBeVisible();
  expect(lastAsked(mock, DIARY)?.get("cursor")).toBe("page-2");
  await expect(page.getByRole("button", { name: "Показать ещё записи" })).toHaveCount(0);

  // Месяц: часть до начала наблюдения не запрашивается, на графике — зона.
  await page.getByRole("radio", { name: "Месяц", exact: true }).click();
  await expect(page).toHaveURL(/period=30/);
  await expect(page.getByText("Настроение, 20 сентября — 19 октября")).toBeVisible();
  await expect(page.getByText("Наблюдение с 22.09").first()).toBeVisible();
  await expect
    .poll(() => lastAsked(mock, `${DIARY}/moods`)?.toString())
    .toBe("start=2026-09-22&end=2026-10-19");
  await shoot(page, testInfo, "06b-diary-month.png");

  await page.getByRole("radio", { name: "3 месяца" }).click();
  await expect(page).toHaveURL(/period=90/);
  await expect(page.getByText(/^Точка — среднее за неделю\./)).toBeVisible();
  await shoot(page, testInfo, "06v-diary-3-months.png");

  await page.getByRole("radio", { name: "Период…" }).click();
  await expect(page.getByRole("dialog", { name: "Выбор периода" })).toBeVisible();
  await shoot(page, testInfo, "06g-diary-calendar.png");
  await page.keyboard.press("Escape");

  // Находка «Разобрать» ведёт в «Предложения» карточки с этим предложением.
  await page.getByRole("radio", { name: "2 недели", exact: true }).click();
  await page.getByRole("link", { name: "Разобрать: Нарушение сна" }).click();
  await expect(page).toHaveURL(new RegExp(`/patients/${LEBEDEVA}/proposals\\?proposal=f-sleep`));
});

test("diary tab: «Открыть запись» on an old point loads its page, shows all kinds and highlights it", async ({
  page,
}) => {
  const mock = diaryMock();
  await openDiary(page, mock);
  await expect(page.getByText("Спокойный день, гуляли с дочерью.")).toBeVisible();
  await page.getByRole("radio", { name: "Голос", exact: true }).click();

  // 14.10 — пятая отметка назад от сегодняшней; её запись на второй странице.
  const chart = page.getByRole("group", { name: /^График настроения/ });
  await chart.focus();
  for (const _ of [1, 2, 3, 4, 5]) {
    await page.keyboard.press("ArrowLeft");
  }
  await expect(chart.getByText("14 октября, среда", { exact: true })).toBeVisible();
  await page.keyboard.press("Enter");

  const row = page.locator("#entry-daily-2026-10-14");
  await expect(row).toBeFocused();
  await expect(row).toHaveClass(/bg-primary-soft/);
  await expect(page.getByRole("radio", { name: "Все", exact: true })).toBeChecked();
  expect(lastAsked(mock, DIARY)?.get("cursor")).toBe("page-2");

  // Esc с кнопки поповера закрывает его и возвращает фокус на график.
  await chart.focus();
  await page.keyboard.press("Home");
  const oldest = chart.getByText("6 октября, вторник", { exact: true });
  await expect(oldest).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(chart.getByRole("button", { name: "Открыть запись" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(oldest).toHaveCount(0);
  await expect(chart).toBeFocused();

  // Отметка 6.10 без записи: пациент её скрыл.
  await page.keyboard.press("Home");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("status").filter({ hasText: "Запись недоступна" })).toBeVisible();
});

test("diary tab: an empty period says there are no entries", async ({ page }, testInfo) => {
  const mock = diaryMock({ empty: true });
  await openDiary(page, mock, `/doctor/patients/${LEBEDEVA}/diary?from=2026-09-22&to=2026-09-28`);

  await expect(page.getByText("За эти дни записей нет")).toBeVisible();
  await expect(page.getByText("За эти дни отметок нет")).toBeVisible();
  expect(lastAsked(mock, DIARY)?.get("start")).toBe("2026-09-22");
  expect(lastAsked(mock, DIARY)?.get("end")).toBe("2026-09-28");
  await shoot(page, testInfo, "06d-diary-empty.png");
});

test("diary tab: a failed load offers to try again", async ({ page }) => {
  let unavailable = true;
  await openWindow(page, `/doctor/patients/${LEBEDEVA}/diary`, async (context) => {
    await mockDiary(context, diaryMock());
    await context.route(new RegExp(`${DIARY}\\?`), (route) =>
      unavailable
        ? route.fulfill({ status: 503, json: { detail: "Diary is temporarily unavailable" } })
        : route.fallback(),
    );
  });

  const failure = page.getByRole("alert").filter({ hasText: "Не удалось загрузить дневник" });
  await expect(failure).toBeVisible();
  unavailable = false;
  await failure.getByRole("button", { name: "Повторить" }).click();
  await expect(page.getByText("Спокойный день, гуляли с дочерью.")).toBeVisible();
});

test("diary tab: a period the API refuses is named, without «try again»", async ({ page }) => {
  await openDiary(page, diaryMock({ refused: true }));

  const refused = page.getByRole("alert").filter({ hasText: "Период не подходит" });
  await expect(refused).toHaveText(
    "Период не подходит: выберите от 7 до 90 дней наблюдения, не позже сегодня",
  );
  await expect(refused.getByRole("button", { name: "Повторить" })).toHaveCount(0);
});

test("diary tab: under 7 days of observation the findings are still there", async ({ page }) => {
  const mock = diaryMock();
  await openWindow(page, `/doctor/patients/${LEBEDEVA}/diary`, async (context) => {
    await mockDiary(context, mock);
    await context.route(`**/api/v1/patients/${LEBEDEVA}`, (route) =>
      route.fulfill({ json: { ...patient(LEBEDEVA), created_at: "2026-10-16T08:00:00Z" } }),
    );
  });

  await expect(page.getByText("Дневник откроется 22.10, с 7-го дня наблюдения.")).toBeVisible();
  await expect(page.getByText("Нарушение сна")).toBeVisible();
  await expect(page.getByRole("link", { name: "Разобрать: Нарушение сна" })).toBeVisible();
  expect(mock.asked.filter((u) => u.pathname.startsWith(DIARY))).toHaveLength(0);
});
