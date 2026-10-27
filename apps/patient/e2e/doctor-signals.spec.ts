import { expect, test } from "@playwright/test";
import { type Alert, alert, KOTOV, openSignals } from "./doctor-signals-mock";

/** Экран «Сигналы» кабинета врача (спека #95) на подменённом API. Данные — `doctor-signals-mock.ts`. */

test("doctor resolves a mandatory signal with a decision and a reason", async ({ page }) => {
  const posted = await openSignals(page);

  const fresh = page.getByRole("region", { name: "Новые" });
  await expect(fresh.getByRole("link")).toHaveText([/Лебедева/, /Котов/]);
  await fresh.getByRole("link", { name: /Лебедева/ }).click();
  await expect(page.getByText("Порог: от 1", { exact: true })).toBeVisible();
  await page.getByLabel("Риск не высокий: наблюдение продолжается").check();
  const save = page.getByRole("button", { name: "Сохранить решение" });
  await save.click();
  // Без причины «Обязательно» не сохраняется: фокус уходит в поле причины.
  await expect(page.getByLabel("Причина решения, обязательно")).toBeFocused();
  expect(posted).toEqual([]);
  await page
    .getByLabel("Причина решения, обязательно")
    .fill("Связались, план — контакт через неделю");
  await page.screenshot({
    path: test.info().outputPath("signals-mandatory-1280.png"),
    fullPage: true,
  });
  await save.click();

  await expect
    .poll(() => posted)
    .toEqual([
      {
        path: "/api/v1/alerts/mandatory/resolve",
        body: { decision_id: "risk-not-high", reason: "Связались, план — контакт через неделю" },
      },
    ]);
});

test("doctor snoozes a desirable signal for a week", async ({ page }) => {
  const posted = await openSignals(page);

  await page.getByRole("link", { name: /Котов/ }).click();
  await expect(page.getByText("Желательно от 10")).toBeVisible();
  await expect(page.getByText("Обязательно от 20")).toBeVisible();
  await expect(page.getByText(/^\+4 за 2 недели$/)).toBeVisible();
  await page.screenshot({
    path: test.info().outputPath("signals-desirable-1280.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Отложить до…" }).click();
  await page.getByRole("button", { name: /^Отложить до \d+ \S+$/ }).click();

  // Маршрутизация откладывается целиком, через ручку направления (#35).
  await expect
    .poll(() => posted.map((p) => p.path))
    .toEqual([`/api/v1/patients/${KOTOV}/referral`]);
  const body = posted[0]?.body as { decision: string; alert_ids: string[]; until: string };
  expect([body.decision, body.alert_ids]).toEqual(["snooze", ["desirable"]]);
  expect(body.until).toMatch(/^\d{4}-\d{2}-\d{2}$/);
});

test("doctor confirms a referral with a comment for the psychiatrist", async ({ page }) => {
  const posted = await openSignals(page);

  await page.getByRole("link", { name: /Котов/ }).click();
  await page.getByRole("button", { name: "Направить к психиатру" }).click();
  const sheet = page.getByRole("dialog", { name: "Направление к психиатру" });
  await expect(sheet.getByText("Желательно от 10")).toBeVisible();
  await sheet.getByLabel("Комментарий для психиатра").fill("Бессонница три недели");
  await page.screenshot({
    path: test.info().outputPath("referral-sheet-1280.png"),
    fullPage: true,
  });
  await sheet.getByRole("button", { name: "Подтвердить направление" }).click();

  await expect
    .poll(() => posted)
    .toEqual([
      {
        path: `/api/v1/patients/${KOTOV}/referral`,
        body: { decision: "confirm", comment: "Бессонница три недели", alert_ids: ["desirable"] },
      },
    ]);
  await expect(sheet).toBeHidden();
});

test("Esc closes the snooze popover first, then the referral sheet", async ({ page }) => {
  await openSignals(page);

  await page.getByRole("link", { name: /Котов/ }).click();
  await page.getByRole("button", { name: "Направить к психиатру" }).click();
  const sheet = page.getByRole("dialog", { name: "Направление к психиатру" });
  await sheet.getByRole("button", { name: "Отложить до…" }).click();
  const popover = sheet.getByRole("heading", { name: "Отложить сигнал до" });
  await expect(popover).toBeVisible();

  // Аннотация кадра 11: Esc закрывает верхний слой — сначала поповер, затем лист.
  await page.keyboard.press("Escape");
  await expect(popover).toBeHidden();
  await expect(sheet).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(sheet).toBeHidden();
});

test("doctor declines a referral only with a reason and a comment", async ({ page }) => {
  const posted = await openSignals(page);

  await page.getByRole("link", { name: /Котов/ }).click();
  await page.getByRole("button", { name: "Направить к психиатру" }).click();
  const sheet = page.getByRole("dialog", { name: "Направление к психиатру" });
  await sheet.getByRole("button", { name: "Отклонить с причиной" }).click();
  const decline = sheet.getByRole("button", { name: "Отклонить направление" });
  await expect(decline).toBeDisabled();
  await expect(sheet.getByText("Выберите причину")).toBeVisible();
  await sheet.getByLabel("Пациент отказался от консультации").check();
  // Данные медика #93: комментарий к отказу обязателен при любой причине.
  await expect(sheet.getByText("Напишите комментарий")).toBeVisible();
  await sheet.getByLabel("Комментарий, обязательно").fill("Обсудим через месяц");
  await page.screenshot({
    path: test.info().outputPath("referral-decline-1280.png"),
    fullPage: true,
  });
  await decline.click();

  await expect
    .poll(() => posted.map((p) => p.body))
    .toEqual([
      {
        decision: "decline",
        comment: "Обсудим через месяц",
        reason_id: "patient-declined",
        alert_ids: ["desirable"],
      },
    ]);
});

test("sheet keeps the signals the doctor saw and shows a new mandatory one", async ({ page }) => {
  const extra: Alert[] = [];
  // Кабинет узнаёт о новом сигнале по WebSocket и перечитывает списки (спека #13).
  let socket: { send: (message: string) => void } | null = null;
  const posted = await openSignals(page, extra, (context) =>
    context.routeWebSocket("**/api/v1/ws", (ws) => {
      socket = ws;
      ws.send(JSON.stringify({ type: "ready", data: {} }));
    }),
  );
  await page.getByRole("link", { name: /Котов/ }).click();
  await page.getByRole("button", { name: "Направить к психиатру" }).click();
  const sheet = page.getByRole("dialog", { name: "Направление к психиатру" });
  await expect(sheet).toBeVisible();

  // Пока лист открыт, пришло «Обязательно» по маршрутизации: кабинет перечитывает сигналы.
  extra.push(
    alert({
      id: "severe",
      patient_id: KOTOV,
      rule_id: "routing-depression-severe",
      kind: "routing",
      title: "Тяжёлая депрессивная симптоматика",
    }),
  );
  await expect.poll(() => socket !== null).toBe(true);
  (socket as unknown as { send: (message: string) => void }).send(
    JSON.stringify({ type: "alert.created", data: { alert_id: "severe" } }),
  );

  await expect(sheet.getByText("Новый сигнал «Обязательно»:")).toBeVisible();
  await sheet.getByRole("button", { name: "Подтвердить направление" }).click();
  // Решение — по тем сигналам, что врач видел; сервер ответит 409, если набор устарел.
  await expect
    .poll(() =>
      posted
        .filter((p) => p.path.endsWith("/referral"))
        .map((p) => (p.body as { alert_ids: string[] }).alert_ids),
    )
    .toEqual([["desirable"]]);
});
