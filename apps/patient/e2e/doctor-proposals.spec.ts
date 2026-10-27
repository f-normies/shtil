import { expect, type Page, test } from "@playwright/test";
import {
  type Item,
  initial,
  LEBEDEVA,
  mockQueue,
  type QueueOptions,
  twins,
} from "./doctor-proposals-mock";
import { mockSessionApi } from "./session-api-mock";

/** Экран «Предложения» и раздел карточки (спека #96) на подменённом API. */

async function open(
  page: Page,
  {
    items = initial(),
    path = "/doctor/proposals",
    ...options
  }: QueueOptions & { items?: Item[]; path?: string } = {},
) {
  await page.setViewportSize({ width: 1280, height: 952 });
  await mockSessionApi(page.context(), "doctor");
  const api = await mockQueue(page.context(), items, options);
  await page.goto(path);
  return api;
}

/** Подробности проявляются 150 мс, решённая строка уходит 150 мс: снимок — после анимаций. */
async function shot(page: Page, name: string) {
  // Отменённая анимация отклоняет `finished` с AbortError: её конец ждать не нужно.
  await page.evaluate(() =>
    Promise.all(document.getAnimations().map((a) => a.finished.catch(() => undefined))),
  );
  await page.screenshot({ path: test.info().outputPath(name), fullPage: true });
}

const list = (page: Page) => page.locator("section[aria-labelledby^='proposals-']");
const details = (page: Page) => page.getByRole("article");
/** Сообщение о решении; у полосы «Нет связи с сервером» та же роль. */
const notice = (page: Page) =>
  page.getByRole("status").filter({ hasNotText: "Нет связи с сервером" });

test("queue groups proposals by patient and shows the finding with its source", async ({
  page,
}) => {
  await open(page);

  await expect(page.getByRole("heading", { name: "Предложения", level: 1 })).toBeVisible();
  await expect(page.getByRole("link", { name: "Предложения ждут решения: 4" })).toBeVisible();
  await expect(page.getByText("3 из 7")).toBeVisible();
  await expect(list(page).getByRole("heading")).toHaveText([
    /Ершов Павел Ильич.*2/,
    /Лебедева Марина Викторовна.*2/,
  ]);
  const card = details(page);
  await expect(card.getByText("Предложение 1 из 2")).toBeVisible();
  await expect(card.getByText("Предложение системы")).toBeVisible();
  await expect(card.getByRole("heading", { name: "Побочный эффект" })).toBeVisible();
  await expect(card.locator("blockquote")).toContainText("сводит челюсть");
  await expect(card.getByText("Цитата пациента, голосовая запись в дневнике")).toBeVisible();
  await expect(card.getByText("Голосовая запись в дневнике", { exact: true })).toBeVisible();
  await expect(card.getByText("extract-7a3603ce, 19.10.2026")).toBeVisible();
  await expect(card.getByText("Связанное предложение")).toBeVisible();
  await expect(page.getByRole("button", { name: "Подтвердить всё" })).toHaveCount(0);
  await shot(page, "proposals-finding-1280.png");

  // «Открыть» у связанного выбирает его в списке; применимость — сверка с ГРЛС словами.
  await card.getByRole("button", { name: "Открыть: Другой препарат" }).click();
  await expect(details(page).getByText("Найден в ГРЛС")).toBeVisible();
  await expect(page.getByRole("button", { name: /Другой препарат.*выбрано/ })).toBeVisible();
});

test("confirming moves to the next proposal of the same patient, then to the next patient", async ({
  page,
}) => {
  const { posted } = await open(page);

  await details(page).getByRole("button", { name: "Подтвердить предложение" }).click();
  await expect(details(page).getByRole("heading", { name: "Другой препарат" })).toBeVisible();
  await expect(page.getByText("4 из 7")).toBeVisible();

  // Отклонить можно без причины: «Отклонить» активна сразу.
  await details(page).getByRole("button", { name: "Отклонить с причиной" }).click();
  await expect(page.getByLabel("Причина отклонения, необязательно")).toBeFocused();
  await details(page).getByRole("button", { name: "Отклонить", exact: true }).click();
  await expect(details(page).getByRole("heading", { name: "Симптом" })).toBeVisible();
  await expect(list(page).getByRole("heading")).toHaveText([/Лебедева Марина Викторовна.*2/]);

  expect(posted).toEqual([
    { path: "/api/v1/proposals/jaw/confirm", body: {} },
    { path: "/api/v1/proposals/triptan/reject", body: { comment: null } },
  ]);
});

test("keyboard: arrows move between proposals, Enter focuses the confirm button", async ({
  page,
}) => {
  await open(page);

  await page.getByRole("button", { name: /Побочный эффект/ }).click();
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("button", { name: /Симптом.*выбрано/ })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(
    details(page).getByRole("button", { name: "Подтвердить предложение" }),
  ).toBeFocused();
});

test("409 already decided: the row leaves with a notice; 422 keeps the proposal", async ({
  page,
}) => {
  const items = initial();
  await open(page, {
    items,
    replies: {
      jaw: { status: 409, detail: "Proposal is already decided" },
      triptan: { status: 422, detail: "Препарат уже есть в карточке" },
    },
  });

  // Решили в другой вкладке: сервер отвечает 409, а список уже без этого предложения.
  await details(page).getByRole("button", { name: "Подтвердить предложение" }).click();
  await expect(notice(page)).toHaveText("Предложение уже рассмотрено");
  await expect(page.getByRole("button", { name: /Побочный эффект/ })).toHaveCount(0);

  await details(page).getByRole("button", { name: "Подтвердить предложение" }).click();
  await expect(details(page).getByRole("alert")).toContainText("Препарат уже есть в карточке");
  await expect(details(page).getByRole("heading", { name: "Другой препарат" })).toBeVisible();
});

test("a prescription shows its label source in the inspector and is decided in Therapy", async ({
  page,
}) => {
  await open(page);

  await page.getByRole("button", { name: /Назначение: Синтетин/ }).click();
  const card = details(page);
  await expect(card.getByText("Показание: Синтетическое расстройство")).toBeVisible();
  await expect(card.getByText("Возраст от 18 лет")).toBeVisible();
  await expect(card.getByRole("link", { name: "Рассмотреть в разделе «Терапия»" })).toBeVisible();
  await card.getByRole("button", { name: "Открыть основание" }).click();
  const inspector = page.getByRole("complementary", { name: "Источник" });
  await expect(
    inspector.getByText("Назначение «Синтетин» по показанию «Синтетическое расстройство»"),
  ).toBeVisible();
  await shot(page, "proposals-therapy-1280.png");
});

test("tabs keep the status in the address; search goes to the server", async ({ page }) => {
  const { queries } = await open(page);

  await page.getByRole("radio", { name: "Подтверждены" }).check();
  await expect(page).toHaveURL(/status=confirmed/);
  await expect(details(page).getByText("Подтверждено врачом")).toBeVisible();
  await expect(details(page).getByText(/^Подтверждено вами/)).toBeVisible();
  await shot(page, "proposals-confirmed-1280.png");

  await page.getByRole("radio", { name: "Отклонены" }).check();
  await expect(page).toHaveURL(/status=rejected/);
  await expect(details(page).getByText("Причина: приняла позже, уточнила в чате")).toBeVisible();
  await shot(page, "proposals-rejected-1280.png");

  await page.getByRole("radio", { name: "Ждут решения" }).check();
  await page.getByPlaceholder("Поиск по ФИО").fill("ерш");
  await expect(list(page).getByRole("heading")).toHaveText([/Ершов Павел Ильич.*2/]);
  expect(queries.some((q) => q.includes("q=%D0%B5%D1%80%D1%88"))).toBe(true);
  await shot(page, "proposals-pending-1280.png");
});

test("empty queue says that everything is decided", async ({ page }) => {
  await open(page, { items: initial().filter((i) => i.status !== "proposed") });

  await expect(page.getByText("Все предложения разобраны")).toBeVisible();
  await shot(page, "proposals-empty-1280.png");
});

test("the card tab shows the same view without patient groups", async ({ page }) => {
  await open(page, { path: `/doctor/patients/${LEBEDEVA}/proposals` });

  await expect(details(page).getByRole("heading", { name: "Симптом" })).toBeVisible();
  await expect(list(page)).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Назначение: Синтетин/ })).toBeVisible();
  await expect(details(page).getByText("Запись в дневнике", { exact: true })).toBeVisible();
  await shot(page, "proposals-card-1280.png");
});

test("the next page is loaded by the cursor of the previous one", async ({ page }) => {
  const { queries } = await open(page, { pageSize: 3 });

  await expect(page.getByRole("button", { name: /Симптом/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Назначение: Синтетин/ })).toHaveCount(0);
  await page.getByRole("button", { name: "Показать ещё" }).click();
  await expect(page.getByRole("button", { name: /Назначение: Синтетин/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "Показать ещё" })).toHaveCount(0);
  expect(queries.filter((q) => q.includes("status=proposed"))).toEqual([
    "?status=proposed&limit=50",
    "?status=proposed&limit=50&cursor=3",
  ]);
});

// Код-ревью #96: второй щелчок двойного клика не должен подтвердить следующее предложение,
// которое врач ещё не прочитал. Ответ API приходит раньше второго щелчка.
test("a double click on confirm decides exactly one proposal", async ({ page }) => {
  const { posted } = await open(page, { items: twins() });

  const confirm = details(page).getByRole("button", { name: "Подтвердить предложение" });
  await confirm.dblclick({ delay: 150 });
  await expect(details(page).getByRole("heading", { name: "Побочный эффект" })).toBeVisible();
  // Пункт выбран автоматически: кнопки решения включаются через 500 мс после появления.
  await expect(confirm).toBeEnabled();
  expect(posted).toEqual([{ path: "/api/v1/proposals/first/confirm", body: {} }]);
  await expect(notice(page)).toHaveText("Подтверждено: Симптом");
});

test("after a decision focus goes to the selected row, not to the confirm button", async ({
  page,
}) => {
  await open(page);

  await details(page).getByRole("button", { name: "Подтвердить предложение" }).click();
  await expect(page.getByRole("button", { name: /Другой препарат.*выбрано/ })).toBeFocused();
  await expect(notice(page)).toHaveText("Подтверждено: Побочный эффект");
});

test("selection stays where the doctor moved it while the decision was saved", async ({ page }) => {
  const { posted } = await open(page, { decisionDelayMs: 400 });

  await details(page).getByRole("button", { name: "Подтвердить предложение" }).click();
  await page.getByRole("button", { name: /Симптом/ }).click();
  await expect.poll(() => posted.length).toBe(1);
  await expect(page.getByRole("button", { name: /Побочный эффект/ })).toHaveCount(0);
  await expect(details(page).getByRole("heading", { name: "Симптом" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Симптом.*выбрано/ })).toBeVisible();
});

test("a proposal of a patient handed to another doctor (404) leaves the list", async ({ page }) => {
  await open(page, { replies: { jaw: { status: 404, detail: "Proposal not found" } } });

  await details(page).getByRole("button", { name: "Подтвердить предложение" }).click();
  await expect(notice(page)).toHaveText("Предложение больше недоступно");
  await expect(page.getByRole("button", { name: /Побочный эффект/ })).toHaveCount(0);
  await expect(details(page).getByRole("heading", { name: "Другой препарат" })).toBeVisible();
});

// Ответ на решение из прежней вкладки обновляет данные, но не скрывает строку
// в истории подтверждений, которую врач открыл до завершения запроса.
test("a delayed confirmation stays visible after switching to confirmed proposals", async ({
  page,
}) => {
  await open(page);
  let release = () => {};
  const decision = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/v1/proposals/jaw/confirm", async (route) => {
    await decision;
    await route.fallback();
  });

  await details(page).getByRole("button", { name: "Подтвердить предложение" }).click();
  await page.getByRole("radio", { name: "Подтверждены" }).check();
  await expect(details(page).getByText("Подтверждено врачом")).toBeVisible();
  release();

  await expect(details(page).getByRole("heading", { name: "Побочный эффект" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Побочный эффект.*выбрано/ })).toBeVisible();
  await expect(details(page).getByText("Подтверждено врачом")).toBeVisible();
});
