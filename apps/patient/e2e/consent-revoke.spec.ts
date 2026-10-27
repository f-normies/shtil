import type { Schemas } from "@compass/api-client";
import { type BrowserContext, expect, type Page, test } from "@playwright/test";
import { DEFINITION_V1 } from "./assessment-fixtures";
import { mockSessionApi, user } from "./session-api-mock";

/** Отзыв согласия (спека #112): экран пациента, вход после отзыва и пометка у врача. Данные выдуманы. */

const REVOKED_TEXT =
  "Вы отозвали согласие на обработку данных. Вход закрыт. Чтобы вернуться, обратитесь к своему врачу.";
const MIRONOVA = "00000000-0000-4000-8000-0000000000f1";
const ORLOVA = "00000000-0000-4000-8000-0000000000f2";

/**
 * Подмена отзыва поверх `mockSessionApi`: после 204 сессии нет, вход отвечает 403
 * `consent_revoked`. `failFirst` — первый запрос отзыва обрывается, как без сети.
 */
async function mockRevoke(context: BrowserContext, failFirst = false) {
  const state = { attempts: 0, revoked: false };
  await context.route("**/api/v1/me/consent/revoke", async (route) => {
    state.attempts += 1;
    if (failFirst && state.attempts === 1) {
      await route.abort("internetdisconnected");
      return;
    }
    state.revoked = true;
    await route.fulfill({ status: 204, body: "" });
  });
  await context.route("**/api/v1/auth/me", async (route) => {
    if (!state.revoked) {
      await route.fallback();
      return;
    }
    await route.fulfill({ status: 401, json: { status: 401, title: "Сессия завершена" } });
  });
  await context.route("**/api/v1/auth/login", async (route) => {
    await route.fulfill({
      status: 403,
      json: {
        type: "about:blank",
        title: "Forbidden",
        status: 403,
        detail: "Consent revoked",
        code: "consent_revoked",
      },
    });
  });
  return state;
}

async function openRevokeScreen(page: Page): Promise<void> {
  await page.setViewportSize({ width: 360, height: 780 });
  await page.context().route("**/api/v1/me/preferences", (route) =>
    route.fulfill({
      status: 200,
      json: {
        display_name: "Синтетический пациент",
        reminder_time: "21:00",
        mood_palette: "warm",
        story_mode: "voice",
        text_size: "normal",
        onboarding_completed_at: "2026-10-25T00:00:00Z",
        revision: 1,
        updated_at: null,
        timezone: "Europe/Moscow",
      },
    }),
  );
  await page
    .context()
    .route("**/api/v1/me/prescriptions", (route) => route.fulfill({ status: 200, json: [] }));
  await page.context().route("**/api/v1/me/consent", (route) =>
    route.fulfill({
      status: 200,
      json: {
        version: "synthetic-consent-v1",
        given_at: "2026-10-25T00:00:00Z",
        text: "Синтетический текст согласия.",
      },
    }),
  );
  await page.goto("/patient/");
  await expect(
    page.getByText("Напоминание о ежедневной отметке: каждый день в 21:00", { exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Профиль" }).click();
  await expect(page).toHaveURL(/\/patient\/profile$/);
  await page.getByRole("link", { name: "Отозвать согласие" }).click();
  await expect(page).toHaveURL(/\/patient\/consent\/revoke$/);
  await expect(page.getByRole("heading", { name: "Отозвать согласие?" })).toBeVisible();
}

async function screenshot(page: Page, name: string): Promise<void> {
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  // Анимации завершены: подробности пациента появляются плавно (animate-fade-in).
  await page.screenshot({
    path: test.info().outputPath(name),
    fullPage: true,
    animations: "disabled",
  });
}

test("patient revokes consent at 360 px and sees why login is closed", async ({ page }) => {
  await mockSessionApi(page.context(), "patient");
  const revoke = await mockRevoke(page.context());
  await openRevokeScreen(page);

  for (const text of [
    "Отметки, записи, ответы на опросы и сообщения больше не принимаются.",
    "Вы выйдете на всех устройствах. Войти снова не получится.",
    "Останется у врача в медицинской карте. Медицинские данные не удаляются.",
    "Увидит, что согласие отозвано, и дату. Вернуться можно через врача.",
  ]) {
    await expect(page.getByText(text, { exact: true })).toBeVisible();
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await screenshot(page, "consent-revoke-360.png");

  await page.getByRole("button", { name: "Отозвать согласие" }).click();
  const confirm = page.getByRole("alertdialog", { name: "Точно отозвать согласие?" });
  await expect(confirm).toBeVisible();
  // Фокус на безопасной кнопке: удержанный Enter не отзовёт согласие.
  await expect(confirm.getByRole("button", { name: "Отмена" })).toBeFocused();
  // Первая кнопка только открывает подтверждение: запрос уходит после «Да, отозвать».
  expect(revoke.attempts).toBe(0);
  await screenshot(page, "consent-revoke-confirm-360.png");
  await confirm.getByRole("button", { name: "Да, отозвать" }).click();

  await expect(page).toHaveURL(/\/patient\/login\?reason=consent_revoked$/);
  await expect(page.getByRole("status").filter({ hasText: REVOKED_TEXT })).toBeVisible();
  expect(revoke.attempts).toBe(1);
  await screenshot(page, "consent-revoked-login-360.png");

  // Вход с верным паролем после отзыва: 403 consent_revoked, тот же текст и только один раз.
  await page.getByLabel("Электронная почта", { exact: true }).fill("patient@example.invalid");
  await page.getByLabel("Пароль", { exact: true }).fill("synthetic-password");
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(page.getByText("сервис недоступен")).toHaveCount(0);
  await expect(page.getByText(REVOKED_TEXT, { exact: true })).toHaveCount(1);
  await expect(page).toHaveURL(/\/patient\/login/);
});

test("lost connection leaves consent in force and the revoke can be retried", async ({ page }) => {
  await mockSessionApi(page.context(), "patient");
  const revoke = await mockRevoke(page.context(), true);
  await openRevokeScreen(page);

  await page.getByRole("button", { name: "Отозвать согласие" }).click();
  const confirm = page.getByRole("alertdialog", { name: "Точно отозвать согласие?" });
  await confirm.getByRole("button", { name: "Да, отозвать" }).click();
  await expect(confirm.getByRole("alert")).toHaveText(
    "Нет связи с сервером. Проверьте интернет и повторите.",
  );
  await expect(page).toHaveURL(/\/patient\/consent\/revoke$/);

  await confirm.getByRole("button", { name: "Да, отозвать" }).click();
  await expect(page).toHaveURL(/\/patient\/login\?reason=consent_revoked$/);
  expect(revoke.attempts).toBe(2);
});

test("lost successful revoke response clears local answers without claiming its outcome", async ({
  page,
}) => {
  await mockSessionApi(page.context(), "patient");
  let attempts = 0;
  await page.context().route("**/api/v1/me/consent/revoke", async (route) => {
    attempts += 1;
    if (attempts === 1) {
      await route.abort("connectionreset");
      return;
    }
    await route.fulfill({ status: 401, json: { status: 401, title: "Unauthorized" } });
  });
  await openRevokeScreen(page);
  await page.evaluate(async (definition) => {
    const modulePath = "/patient/src/session.ts";
    const { getAssessmentStore } = await import(/* @vite-ignore */ modulePath);
    const store = await getAssessmentStore();
    const owner = await store.getOwnerPointer();
    const request = indexedDB.open("clin-compass-assessments");
    const db = await new Promise<IDBDatabase>((resolve) => {
      request.onsuccess = () => resolve(request.result);
    });
    const tx = db.transaction(["drafts", "outbox"], "readwrite");
    tx.objectStore("drafts").put({
      owner_id: owner.id,
      scale_id: definition.scale.id,
      revision: 1,
      answers: { q1: 0 },
      definition_snapshot: definition,
      updated_at: "2026-10-19T09:00:00Z",
    });
    tx.objectStore("outbox").put({
      owner_id: owner.id,
      submission_id: "00000000-0000-4000-8000-000000000011",
      payload: {
        patient_id: owner.id,
        submission_id: "00000000-0000-4000-8000-000000000011",
        scale_id: definition.scale.id,
        scale_version: 1,
        completed_at: "2026-10-19T09:00:00Z",
        answers: { q1: 0 },
      },
      definition_snapshot: definition,
      state: "pending",
      last_error: null,
      created_at: "2026-10-19T09:00:00Z",
    });
    await new Promise<void>((resolve) => {
      tx.oncomplete = () => resolve();
    });
    db.close();
  }, DEFINITION_V1);
  await page.getByRole("button", { name: "Отозвать согласие" }).click();
  const confirm = page.getByRole("alertdialog");
  await confirm.getByRole("button", { name: "Да, отозвать" }).click();
  await expect(confirm.getByRole("alert")).toContainText("Нет связи");
  await page
    .context()
    .route("**/api/v1/auth/me", (route) =>
      route.fulfill({ status: 401, json: { status: 401, title: "Unauthorized" } }),
    );
  await page.evaluate(async () => {
    const modulePath = "/patient/src/session.ts";
    const { getAssessmentStore } = await import(/* @vite-ignore */ modulePath);
    const store = await getAssessmentStore();
    await store.lockOwnerPointer((await store.getOwnerPointer()).id);
  });
  await confirm.getByRole("button", { name: "Да, отозвать" }).click();
  await expect(page).toHaveURL(/\/patient\/login\?reason=consent_revoke_unknown$/);
  await expect(page.getByRole("status")).toContainText(
    "Не удалось узнать результат отзыва согласия",
  );
  await expect(page.getByText(REVOKED_TEXT, { exact: true })).toHaveCount(0);
  const local = await page.evaluate(async () => {
    const request = indexedDB.open("clin-compass-assessments");
    const db = await new Promise<IDBDatabase>((resolve) => {
      request.onsuccess = () => resolve(request.result);
    });
    const tx = db.transaction(["meta", "drafts", "outbox"], "readonly");
    const read = <T>(request: IDBRequest<T>) =>
      new Promise<T>((resolve) => {
        request.onsuccess = () => resolve(request.result);
      });
    const result = await Promise.all([
      read(tx.objectStore("meta").get("owner_pointer")),
      read(tx.objectStore("drafts").count()),
      read(tx.objectStore("outbox").count()),
    ]);
    db.close();
    return result;
  });
  expect(local).toEqual([undefined, 0, 0]);
});

const DOCTOR = user("doctor");
const REVOKED_AT = "2026-10-18T09:00:00Z";
const SOURCE_REF = { source: "v", locator: "1" };

function summary(
  id: string,
  fullName: string,
  revokedAt: string | null,
): Schemas["PatientSummary"] {
  return {
    access: "lead",
    id,
    full_name: fullName,
    email: "p@example.invalid",
    lead_doctor_id: DOCTOR.id,
    created_at: "2026-09-24T09:00:00Z",
    phone: "+79990000000",
    birth_date: "1985-03-02",
    sex: "female",
    pregnant: null,
    breastfeeding: null,
    child_pugh: null,
    egfr: null,
    egfr_measured_on: null,
    consent_revoked_at: revokedAt,
  };
}

const PATIENTS = [
  summary(ORLOVA, "Орлова Наталья Петровна", null),
  summary(MIRONOVA, "Миронова Елена Игоревна", REVOKED_AT),
];

/** Результат шкалы, собранный до отзыва: врач видит его как раньше. */
function result(patientId: string, scale: "phq-9" | "gad-7", at: string, value: number) {
  return {
    id: `${patientId}-${scale}-${at}`,
    submission_id: at,
    patient_id: patientId,
    scale_id: scale,
    scale_version: 1,
    scale_title: scale === "phq-9" ? "Опросник здоровья пациента (PHQ-9)" : "Шкала тревоги (GAD-7)",
    completed_at: at,
    received_at: at,
    answers: {},
    scores: { total: { value, band: { code: "min", label: "Минимальная", source: SOURCE_REF } } },
    sources: [],
    recorded_by: null,
  };
}

const RESULTS = [
  result(MIRONOVA, "phq-9", "2026-10-12T06:20:00Z", 3),
  result(MIRONOVA, "gad-7", "2026-10-12T06:20:00Z", 4),
  result(ORLOVA, "phq-9", "2026-10-17T06:12:00Z", 7),
];

function definition(id: "phq-9" | "gad-7") {
  return {
    scale: {
      id,
      title: id === "phq-9" ? "Опросник здоровья пациента (PHQ-9)" : "Шкала тревоги (GAD-7)",
      instruction: "За последние 2 недели",
      version: 1,
      purpose: "screening",
      respondent: "patient",
      status: "reviewed",
      items: [],
      license: { terms: "t", allowed: true, source: SOURCE_REF },
      scores: [
        {
          id: "total",
          items: [],
          bands: [{ min: 0, max: 27, code: "min", label: "Минимальная", source: SOURCE_REF }],
        },
      ],
      validation: [SOURCE_REF],
    },
    sources: [],
  };
}

/** Подмена API окна врача v4, как в `doctor-window.spec.ts`. Неизвестное — в подмену сессии. */
async function mockDoctorWindow(context: BrowserContext) {
  await context.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const byPatient = /^\/api\/v1\/patients\/([^/]+)(?:\/(.+))?$/.exec(path);
    const id = byPatient?.[1] ?? "";
    const json = (() => {
      if (path === "/api/v1/patients") return PATIENTS;
      if (path === "/api/v1/alerts" || path === "/api/v1/messaging/unread") return [];
      if (path === "/api/v1/proposals/summary") return { pending: 0, decided_today: 0 };
      if (path === "/api/v1/assessments/clinician-scales") return { items: [] };
      const scale = /^\/api\/v1\/assessments\/scales\/(phq-9|gad-7)$/.exec(path)?.[1];
      if (scale === "phq-9" || scale === "gad-7") return definition(scale);
      if (!byPatient) return null;
      switch (byPatient[2]) {
        case undefined:
          return PATIENTS.find((p) => p.id === id) ?? null;
        case "assessments":
          return { items: RESULTS.filter((r) => r.patient_id === id), next_cursor: null };
        case "assessments/schedule":
          return { today: "2026-10-19", items: [] };
        case "alerts":
        case "proposals":
        case "diagnoses":
        case "prescriptions":
        case "documents":
          return [];
        default:
          return null;
      }
    })();
    if (json === null) {
      await route.fallback();
      return;
    }
    await route.fulfill({ json });
  });
}

const EFFECT =
  "Новые данные от пациента не поступают, вход в кабинет закрыт. Собранные данные остаются в карте.";

test("doctor sees the revoked consent in the list, details and card at 1280 px", async ({
  page,
}) => {
  await page.clock.setFixedTime(new Date("2026-10-19T12:00:00+03:00"));
  await page.setViewportSize({ width: 1280, height: 832 });
  await mockSessionApi(page.context(), "doctor");
  await mockDoctorWindow(page.context());
  await page.goto(`/doctor/?patient=${MIRONOVA}`);

  // Кадр 02в: без сигналов и предложений — в «Остальных», вторая строка — отзыв и дата.
  const rest = page
    .getByRole("listbox", { name: "Пациенты" })
    .getByRole("group", { name: "Остальные" });
  const mironova = rest.getByRole("option", { name: /Миронова/ });
  await expect(mironova).toContainText("Согласие отозвано 18.10");
  await expect(mironova).toHaveAttribute("aria-label", /Согласие отозвано 18\.10/);
  await expect(rest.getByRole("option", { name: /Орлова/ })).not.toContainText("Согласие");
  const banner = page.getByRole("status").filter({ hasText: "Согласие отозвано 18.10.2026" });
  await expect(banner).toContainText(EFFECT);
  // Собранные данные врач видит как раньше.
  await expect(page.getByRole("region", { name: "Состояние", exact: true })).toContainText("PHQ-9");
  await screenshot(page, "doctor-patients-consent-revoked-1280.png");

  // У пациента с действующим согласием плашки нет.
  await rest.getByRole("option", { name: /Орлова/ }).click();
  await expect(page.getByRole("heading", { name: "Орлова Наталья Петровна" })).toBeVisible();
  await expect(page.getByText("Согласие отозвано 18.10.2026")).toHaveCount(0);

  // Кадр 05г: плашка сверху карточки, над всеми разделами.
  await page.goto(`/doctor/patients/${MIRONOVA}`);
  await expect(
    page.getByRole("heading", { name: "Миронова Елена Игоревна", level: 1 }),
  ).toBeVisible();
  await expect(
    page.getByRole("status").filter({ hasText: "Согласие отозвано 18.10.2026" }),
  ).toContainText(EFFECT);
  await screenshot(page, "doctor-card-consent-revoked-1280.png");
});
