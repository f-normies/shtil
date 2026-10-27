import { expect, type Page, test } from "@playwright/test";

const CONSENT_TEXT =
  "Даю согласие на обработку моих персональных данных, включая сведения о состоянии здоровья, лечащим врачом в целях наблюдения и лечения.";
const CURRENT_CONSENT = { version: "2026-10-16-draft", text: CONSENT_TEXT };
const UPDATED_CONSENT = {
  version: "2026-10-20-draft",
  text: "Обновлённый текст согласия\nдля проверки повторного подтверждения.",
};
const CONSENT_LABEL = "Даю согласие на обработку персональных данных";
const CONSENT_UNAVAILABLE = {
  status: 503,
  contentType: "application/problem+json",
  body: JSON.stringify({ status: 503, title: "Synthetic service unavailable" }),
};
const EMAIL_TAKEN = {
  status: 409,
  contentType: "application/problem+json",
  body: JSON.stringify({
    type: "about:blank",
    title: "Conflict",
    status: 409,
    detail: "Email is already registered",
  }),
};
const VERSION_CONFLICT = {
  status: 409,
  contentType: "application/problem+json",
  body: JSON.stringify({
    type: "about:blank",
    title: "Conflict",
    status: 409,
    code: "consent_version_mismatch",
  }),
};

function consentResponse(document: typeof CURRENT_CONSENT) {
  return { status: 200, contentType: "application/json", body: JSON.stringify(document) };
}

async function showRegistration(page: Page): Promise<void> {
  await page.setViewportSize({ width: 360, height: 800 });
  await page.goto("/patient/register?invite=synthetic-invite");
}

async function fillRegistration(page: Page): Promise<void> {
  await page.getByLabel("Фамилия, имя, отчество").fill("Петрова Анна Сергеевна");
  await page.getByLabel("Электронная почта").fill("patient@example.invalid");
  await page.getByLabel("Пароль", { exact: true }).fill("correct horse battery staple");
}

async function refocusWindow(page: Page): Promise<void> {
  await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
}

function gate(): { wait: Promise<void>; release: () => void } {
  let release!: () => void;
  const wait = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { wait, release };
}

test("consent loading failure blocks registration until a successful retry", async ({ page }) => {
  let failures = 0;
  let retries = 0;
  let allowRetry = false;
  const failure = gate();
  await page.route("**/api/v1/patients/consent", async (route) => {
    if (!allowRetry) {
      failures += 1;
      await failure.wait;
      await route.fulfill(CONSENT_UNAVAILABLE);
      return;
    }
    retries += 1;
    await route.fulfill(consentResponse(CURRENT_CONSENT));
  });

  await showRegistration(page);

  await expect(page.getByText("Загружаем текст согласия…")).toBeVisible();
  failure.release();
  await expect(page.getByRole("alert")).toContainText(
    "Ошибка: не удалось загрузить текст согласия",
  );
  await expect(page.getByRole("button", { name: "Создать аккаунт" })).toBeDisabled();
  allowRetry = true;
  await page.getByRole("button", { name: "Повторить загрузку" }).click();

  await expect(page.getByRole("checkbox", { name: CONSENT_LABEL })).not.toBeChecked();
  await expect(page.getByRole("button", { name: "Создать аккаунт" })).toBeDisabled();
  // Полный текст согласия — на отдельном экране (кадр 03), за кнопкой «Прочитать полностью».
  await page.getByRole("button", { name: "Прочитать согласие" }).click();
  await page.getByText("Прочитать полностью").click();
  await expect(page.getByText(CONSENT_TEXT)).toBeVisible();
  await page.getByRole("button", { name: "Вернуться к форме" }).click();
  await expect(page.getByRole("checkbox", { name: CONSENT_LABEL })).not.toBeChecked();
  expect(failures).toBeGreaterThan(0);
  expect(retries).toBeGreaterThan(0);
});

test("consent loading messages reuse one live region", async ({ page }) => {
  let refreshing = false;
  const load = gate();
  const refresh = gate();
  await page.route("**/api/v1/patients/consent", async (route) => {
    await (refreshing ? refresh.wait : load.wait);
    await route.fulfill(consentResponse(CURRENT_CONSENT));
  });

  await showRegistration(page);
  const status = page.getByRole("status");
  await expect(status).toHaveText("Загружаем текст согласия…");
  const region = await status.elementHandle();
  load.release();
  await expect(page.getByRole("checkbox", { name: CONSENT_LABEL })).toBeVisible();
  await expect(status).toHaveText("");
  refreshing = true;
  await refocusWindow(page);
  await expect(status).toHaveText("Обновляем текст согласия…");
  refresh.release();

  await expect(status).toHaveText("");
  expect(await status.evaluate((node, initial) => node === initial, region)).toBe(true);
});

test("registration submits the displayed version and keeps the email conflict message", async ({
  page,
}) => {
  let registrationBody: Record<string, unknown> | undefined;
  await page.route("**/api/v1/patients/consent", (route) =>
    route.fulfill(consentResponse(CURRENT_CONSENT)),
  );
  await page.route("**/api/v1/patients/register", async (route) => {
    registrationBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill(EMAIL_TAKEN);
  });

  await showRegistration(page);
  await fillRegistration(page);
  await page.getByRole("checkbox", { name: CONSENT_LABEL }).check();
  await page.getByRole("button", { name: "Создать аккаунт" }).click();

  await expect(page.getByRole("alert")).toContainText("этот email уже зарегистрирован");
  expect(registrationBody).toMatchObject({
    invite_token: "synthetic-invite",
    email: "patient@example.invalid",
    consent_personal_data: true,
    consent_version: CURRENT_CONSENT.version,
  });
});

test("a failed background refresh keeps the loaded consent and allows submission", async ({
  page,
}) => {
  let consentRequests = 0;
  let failRefresh = false;
  const refresh = gate();
  let registrationBody: Record<string, unknown> | undefined;
  await page.route("**/api/v1/patients/consent", async (route) => {
    consentRequests += 1;
    if (!failRefresh) {
      await route.fulfill(consentResponse(CURRENT_CONSENT));
      return;
    }
    await refresh.wait;
    await route.fulfill(CONSENT_UNAVAILABLE);
  });
  await page.route("**/api/v1/patients/register", async (route) => {
    registrationBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill(EMAIL_TAKEN);
  });

  await showRegistration(page);
  await fillRegistration(page);
  await page.getByRole("checkbox", { name: CONSENT_LABEL }).check();
  const requestsBeforeRefresh = consentRequests;
  failRefresh = true;
  await refocusWindow(page);
  await expect.poll(() => consentRequests).toBeGreaterThan(requestsBeforeRefresh);
  await expect(page.getByRole("button", { name: "Создать аккаунт" })).toBeDisabled();
  refresh.release();

  // Сервер всё равно сверяет версию: прочитанный текст остаётся, отправка доступна.
  await expect(page.getByRole("button", { name: "Создать аккаунт" })).toBeEnabled();
  await expect(page.getByRole("checkbox", { name: CONSENT_LABEL })).toBeChecked();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.getByRole("button", { name: "Создать аккаунт" }).click();
  await expect(page.getByRole("alert")).toContainText("этот email уже зарегистрирован");
  expect(registrationBody?.consent_version).toBe(CURRENT_CONSENT.version);
});

test("a consent refresh blocks submission until the changed version is accepted", async ({
  page,
}) => {
  let consentRequests = 0;
  let refreshEnabled = false;
  const refresh = gate();
  await page.route("**/api/v1/patients/consent", async (route) => {
    consentRequests += 1;
    if (refreshEnabled) await refresh.wait;
    await route.fulfill(consentResponse(refreshEnabled ? UPDATED_CONSENT : CURRENT_CONSENT));
  });

  await showRegistration(page);
  await fillRegistration(page);
  await page.getByRole("checkbox", { name: CONSENT_LABEL }).check();
  await expect(page.getByRole("button", { name: "Создать аккаунт" })).toBeEnabled();

  const requestsBeforeRefresh = consentRequests;
  refreshEnabled = true;
  await refocusWindow(page);
  await expect.poll(() => consentRequests).toBeGreaterThan(requestsBeforeRefresh);
  await expect(page.getByRole("button", { name: "Создать аккаунт" })).toBeDisabled();
  await expect(page.getByText("Обновляем текст согласия…")).toBeVisible();
  refresh.release();

  await expect(page.getByRole("checkbox", { name: CONSENT_LABEL })).not.toBeChecked();
  await expect(page.getByRole("alert")).toContainText("Ошибка: текст согласия изменился");
  await expect(page.getByRole("button", { name: "Создать аккаунт" })).toBeDisabled();

  // «Принимаю» на экране новой редакции ставит галочку в форме.
  await page.getByRole("button", { name: "Прочитать согласие" }).click();
  await page.getByText("Прочитать полностью").click();
  await expect(page.getByText(UPDATED_CONSENT.text)).toBeVisible();
  await page.getByRole("button", { name: "Принимаю" }).click();
  await expect(page.getByRole("checkbox", { name: CONSENT_LABEL })).toBeChecked();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Создать аккаунт" })).toBeEnabled();
});

test("version conflict reloads consent and requires a fresh confirmation", async ({ page }) => {
  let consentRequests = 0;
  let registrationStarted = false;
  const registrationBodies: Record<string, unknown>[] = [];
  await page.route("**/api/v1/patients/consent", async (route) => {
    consentRequests += 1;
    await route.fulfill(consentResponse(registrationStarted ? UPDATED_CONSENT : CURRENT_CONSENT));
  });
  await page.route("**/api/v1/patients/register", async (route) => {
    registrationBodies.push(route.request().postDataJSON() as Record<string, unknown>);
    if (registrationBodies.at(-1)?.consent_version === CURRENT_CONSENT.version) {
      registrationStarted = true;
      await route.fulfill(VERSION_CONFLICT);
      return;
    }
    await route.fulfill(EMAIL_TAKEN);
  });

  await showRegistration(page);
  await fillRegistration(page);
  await page.getByRole("checkbox", { name: CONSENT_LABEL }).check();
  await page.getByRole("button", { name: "Создать аккаунт" }).click();

  await expect(page.getByRole("alert")).toContainText("Ошибка: текст согласия изменился");
  await expect(page.getByRole("checkbox", { name: CONSENT_LABEL })).not.toBeChecked();
  await expect(page.getByLabel("Фамилия, имя, отчество")).toHaveValue("Петрова Анна Сергеевна");
  await expect(page.getByLabel("Электронная почта")).toHaveValue("patient@example.invalid");
  await expect(page.getByLabel("Пароль", { exact: true })).toHaveValue(
    "correct horse battery staple",
  );
  await expect(page.getByRole("button", { name: "Создать аккаунт" })).toBeDisabled();
  expect(registrationBodies).toHaveLength(1);
  expect(registrationBodies[0]?.consent_version).toBe(CURRENT_CONSENT.version);

  await page.getByRole("checkbox", { name: CONSENT_LABEL }).check();
  await page.getByRole("button", { name: "Создать аккаунт" }).click();

  await expect(page.getByRole("alert")).toContainText("этот email уже зарегистрирован");
  expect(registrationBodies).toHaveLength(2);
  expect(registrationBodies[1]?.consent_version).toBe(UPDATED_CONSENT.version);
  expect(consentRequests).toBeGreaterThan(1);
});

test("a failed reload after a version conflict hides the outdated consent", async ({ page }) => {
  let conflict = false;
  let reloadFails = true;
  await page.route("**/api/v1/patients/consent", async (route) => {
    if (conflict && reloadFails) {
      await route.fulfill(CONSENT_UNAVAILABLE);
      return;
    }
    await route.fulfill(consentResponse(conflict ? UPDATED_CONSENT : CURRENT_CONSENT));
  });
  await page.route("**/api/v1/patients/register", async (route) => {
    conflict = true;
    await route.fulfill(VERSION_CONFLICT);
  });

  await showRegistration(page);
  await fillRegistration(page);
  await page.getByRole("checkbox", { name: CONSENT_LABEL }).check();
  await page.getByRole("button", { name: "Создать аккаунт" }).click();

  // Сервер отверг показанную редакцию: принимать её снова бессмысленно.
  const loadError = page
    .getByRole("alert")
    .filter({ hasText: "не удалось загрузить текст согласия" });
  await expect(loadError).toBeVisible();
  await expect(page.getByRole("checkbox", { name: CONSENT_LABEL })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Создать аккаунт" })).toBeDisabled();
  reloadFails = false;
  await page.getByRole("button", { name: "Повторить загрузку" }).click();

  await expect(page.getByRole("checkbox", { name: CONSENT_LABEL })).not.toBeChecked();
  await expect(loadError).toHaveCount(0);
});
