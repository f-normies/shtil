import { expect, type Page, test } from "@playwright/test";

const PATIENT_ID = "00000000-0000-4000-8000-000000000098";

function json(status: number, body: unknown) {
  return {
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  };
}

async function mockPatientPreferences(
  page: Page,
  options: {
    ownerId?: string;
    displayName?: string;
    completedAt?: string | null;
    activePrescription?: boolean;
    prescriptionLoadFailures?: number;
    pillSaveFailures?: number;
  } = {},
) {
  const ownerId = options.ownerId ?? PATIENT_ID;
  const preferences = {
    display_name: options.displayName ?? "Марина Викторовна",
    reminder_time: "21:00",
    mood_palette: "warm",
    story_mode: "voice",
    text_size: "normal",
    onboarding_completed_at: options.completedAt ?? null,
    revision: 0,
    timezone: "Europe/Moscow",
    updated_at: null as string | null,
  };
  const prescriptions = options.activePrescription
    ? [
        {
          appearance: null,
          appearance_origin: "instruction",
          appearance_revision: 2,
          default_appearance: {
            color: "white",
            description: "Светлая круглая таблетка.",
            shape: "round",
            source: {
              locator: "раздел 4",
              source: "instruction",
              title: "Инструкция по медицинскому применению",
              url: "https://example.invalid/instruction",
            },
          },
          dose_mg: 25,
          ends_on: null,
          inn: "вортиоксетин",
          instructions: [],
          prescription_id: "00000000-0000-4000-8000-000000000088",
          reactions: [],
          schedule: "ежедневно",
          started_at: "2026-10-20T09:00:00Z",
          times_per_day: 1,
          trade_name: "Авиандр",
        },
      ]
    : [];
  let completion: Record<string, unknown> | null = null;
  let profileUpdate: Record<string, unknown> | null = null;
  let pillUpdate: Record<string, unknown> | null = null;
  let prescriptionLoadFailures = options.prescriptionLoadFailures ?? 0;
  let prescriptionLoadRequests = 0;
  let pillSaveFailures = options.pillSaveFailures ?? 0;
  let loggedOut = false;
  let apiOffline = false;

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (apiOffline) {
      await route.abort("failed");
      return;
    }
    if (pathname === "/api/v1/auth/me") {
      if (loggedOut) {
        await route.fulfill(json(401, { detail: "Сессия завершена." }));
        return;
      }
      await route.fulfill(
        json(200, {
          id: ownerId,
          email: "patient@example.invalid",
          role: "patient",
          full_name: options.displayName ?? "Марина Викторовна",
        }),
      );
      return;
    }
    if (pathname === "/api/v1/me/preferences" && request.method() === "GET") {
      await route.fulfill(json(200, preferences));
      return;
    }
    if (pathname === "/api/v1/diary/context") {
      await route.fulfill(
        json(200, {
          daily_status: "missing",
          month_entry_count: 0,
          observation_from: "2026-10-01",
          server_now: "2026-10-25T09:00:00Z",
          timezone: "Europe/Moscow",
          today: "2026-10-25",
        }),
      );
      return;
    }
    if (pathname === "/api/v1/assessments/schedule") {
      await route.fulfill(json(200, { today: "2026-10-25", items: [] }));
      return;
    }
    if (
      pathname === "/api/v1/diary/entries" ||
      pathname === "/api/v1/diary/intake" ||
      pathname.endsWith("/messages")
    ) {
      await route.fulfill(json(200, { items: [], next_cursor: null }));
      return;
    }
    if (pathname === "/api/v1/me/doses" && request.method() === "GET") {
      await route.fulfill(json(200, { day: "2026-10-25", doses: [] }));
      return;
    }
    if (pathname === "/api/v1/me/preferences" && request.method() === "PATCH") {
      profileUpdate = request.postDataJSON() as Record<string, unknown>;
      const { expected_revision: _revision, ...changed } = profileUpdate;
      Object.assign(preferences, changed, {
        revision: preferences.revision + 1,
        updated_at: "2026-10-25T09:10:00Z",
      });
      await route.fulfill(json(200, preferences));
      return;
    }
    if (pathname === "/api/v1/me/preferences/complete" && request.method() === "POST") {
      completion = request.postDataJSON() as Record<string, unknown>;
      Object.assign(preferences, completion, {
        revision: preferences.revision + 1,
        onboarding_completed_at: "2026-10-25T09:00:00Z",
      });
      await route.fulfill(json(200, preferences));
      return;
    }
    if (pathname === "/api/v1/me/prescriptions" && request.method() === "GET") {
      prescriptionLoadRequests += 1;
      if (prescriptionLoadFailures > 0) {
        prescriptionLoadFailures -= 1;
        await route.fulfill(json(503, { detail: "Временная ошибка загрузки назначений." }));
        return;
      }
      await route.fulfill(json(200, prescriptions));
      return;
    }
    if (
      /\/api\/v1\/me\/prescriptions\/[^/]+\/appearance$/.test(pathname) &&
      request.method() === "PUT"
    ) {
      pillUpdate = request.postDataJSON() as Record<string, unknown>;
      if (pillSaveFailures > 0) {
        pillSaveFailures -= 1;
        await route.fulfill(json(503, { detail: "Временная ошибка сохранения вида." }));
        return;
      }
      const appearance = pillUpdate.appearance as { shape: string; color: string };
      const prescription = prescriptions[0];
      if (!prescription) {
        await route.fulfill(json(404, { detail: "Действующее назначение не найдено." }));
        return;
      }
      Object.assign(prescription, {
        appearance,
        appearance_origin: "patient",
        appearance_revision: prescription.appearance_revision + 1,
      });
      await route.fulfill(
        json(200, {
          appearance,
          appearance_origin: prescription.appearance_origin,
          appearance_revision: prescription.appearance_revision,
        }),
      );
      return;
    }
    if (pathname === "/api/v1/me/consent" && request.method() === "GET") {
      await route.fulfill(
        json(200, {
          version: "consent-2026-10",
          given_at: "2026-10-25T08:00:00Z",
          text: "Тестовая редакция согласия.",
        }),
      );
      return;
    }
    if (pathname === "/api/v1/assessments/scales") {
      await route.fulfill(json(200, { items: [] }));
      return;
    }
    if (pathname === "/api/v1/assessments") {
      await route.fulfill(json(200, { items: [], next_cursor: null }));
      return;
    }
    if (pathname === "/api/v1/auth/logout") {
      loggedOut = true;
      await route.fulfill({ status: 204, body: "" });
      return;
    }
    await route.fulfill(json(200, {}));
  });

  return {
    completion: () => completion,
    profileUpdate: () => profileUpdate,
    pillUpdate: () => pillUpdate,
    prescriptionLoadRequests: () => prescriptionLoadRequests,
    allowPrescriptionLoad: () => {
      prescriptionLoadFailures = 0;
    },
    preferences,
    setOffline: (offline: boolean) => {
      apiOffline = offline;
    },
  };
}

test("new patient opening medicines directly is sent to onboarding", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await mockPatientPreferences(page);
  await page.goto("/patient/medications");

  await expect(page).toHaveURL(/\/patient\/onboarding$/);
  await expect(page.getByRole("heading", { name: "Добро пожаловать в Штиль" })).toBeVisible();
});

test("skipping the first welcome screen saves defaults and opens Today", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  const api = await mockPatientPreferences(page);
  await page.goto("/patient/");

  await expect(page.getByRole("heading", { name: "Добро пожаловать в Штиль" })).toBeVisible();
  await page.getByRole("button", { name: "Пропустить" }).click();

  await expect(page).toHaveURL(/\/patient\/$/);
  await expect(page.getByRole("heading", { name: /, Марина Викторовна$/ })).toBeVisible();
  expect(api.completion()).toEqual({ expected_revision: 0 });
  expect(api.preferences.onboarding_completed_at).toBe("2026-10-25T09:00:00Z");
});

test("welcome can retry prescription loading after it fails before onboarding starts", async ({
  page,
}) => {
  await page.setViewportSize({ width: 360, height: 800 });
  const api = await mockPatientPreferences(page, {
    activePrescription: true,
    prescriptionLoadFailures: 100,
  });
  await page.goto("/patient/onboarding");

  await expect(page.getByRole("heading", { name: "Добро пожаловать в Штиль" })).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("Не удалось загрузить назначения");
  await expect(page.getByRole("button", { name: "Начать" })).toBeDisabled();
  api.allowPrescriptionLoad();
  await page.getByRole("button", { name: "Повторить загрузку назначений" }).click();

  await expect(page.getByRole("button", { name: "Начать" })).toBeEnabled();
  await page.getByRole("button", { name: "Начать" }).click();
  await page.getByLabel("Имя").fill("Марина");
  await page.getByRole("button", { name: "Дальше" }).click();
  await page.getByRole("button", { name: "Дальше" }).click();
  await page.getByRole("button", { name: /Зелёный/ }).click();
  await page.getByRole("button", { name: "Дальше" }).click();
  await page.getByRole("button", { name: /Текстом/ }).click();
  await page.getByRole("button", { name: "Дальше" }).click();

  await expect(page.getByRole("heading", { name: "Как выглядят ваши таблетки?" })).toBeVisible();
  expect(api.prescriptionLoadRequests()).toBeGreaterThanOrEqual(3);
});

test("full onboarding saves preferences and an active prescription before opening the profile", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 360, height: 800 });
  const api = await mockPatientPreferences(page, {
    activePrescription: true,
    pillSaveFailures: 1,
  });
  await page.goto("/patient/");

  await expect(page.getByRole("heading", { name: "Добро пожаловать в Штиль" })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("welcome-360.png") });
  await page.getByRole("button", { name: "Начать" }).click();

  await page.getByLabel("Имя").fill("Марина");
  await page.getByRole("button", { name: "Дальше" }).click();
  await page.getByRole("button", { name: "14:00" }).click();
  await page.getByRole("button", { name: "Дальше" }).click();
  await page.getByRole("button", { name: /Зелёный/ }).click();
  await page.getByRole("button", { name: "Дальше" }).click();
  await page.getByRole("button", { name: /Текстом/ }).click();
  await page.getByRole("button", { name: "Дальше" }).click();

  await expect(page.getByRole("heading", { name: "Как выглядят ваши таблетки?" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Авиандр" })).toBeVisible();
  await page.getByRole("button", { name: "Овальная" }).click();
  await page.getByRole("button", { name: "Персиковая" }).click();
  // «Дальше» сохраняет выбор; после ошибки шаг остаётся, повторное «Дальше» пробует снова.
  await page.getByRole("button", { name: "Дальше" }).click();
  await expect(page.getByRole("alert")).toContainText("Не удалось сохранить вид таблетки");
  await expect(page.getByRole("heading", { name: "Как выглядят ваши таблетки?" })).toBeVisible();
  await page.getByRole("button", { name: "Дальше" }).click();
  await expect(page.getByRole("heading", { name: "Какой размер текста удобнее?" })).toBeVisible();
  expect(api.pillUpdate()).toEqual({
    expected_revision: 2,
    appearance: { shape: "oval", color: "peach" },
  });

  await page.getByRole("button", { name: /Крупный/ }).click();
  await page.getByRole("button", { name: "Дальше" }).click();
  await expect(page.getByRole("heading", { name: "Всё готово, Марина" })).toBeVisible();
  await expect(page.getByText("Персиковая овальная")).toBeVisible();
  await page.getByRole("button", { name: "Открыть «Сегодня»" }).click();

  await expect(page.getByRole("heading", { name: /, Марина$/ })).toBeVisible();
  await page.getByRole("link", { name: "Профиль" }).click();
  await expect(page.getByRole("heading", { name: "Профиль" })).toBeVisible();
  await expect(page.getByRole("link", { name: /Цвета настроения/ })).toContainText("Зелёный");
  await expect(page.getByRole("link", { name: /Как рассказывать о дне/ })).toContainText("Текстом");
  await expect(page.getByRole("link", { name: /Размер текста/ })).toContainText("Крупный");
  await expect(page.getByRole("navigation", { name: "Основная навигация" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Профиль" })).toHaveAttribute("aria-current", "page");
  await page.getByRole("link", { name: "Сегодня" }).click();
  await expect(page).toHaveURL(/\/patient\/$/);
  await expect(page.getByRole("heading", { name: /, Марина$/ })).toBeVisible();
  await page.getByRole("link", { name: "Профиль" }).click();
  await expect(page).toHaveURL(/\/patient\/profile$/);
  await expect(page.getByRole("link", { name: "Дневник" })).toHaveAttribute(
    "href",
    /\/patient\/diary$/,
  );
  await expect(page.getByRole("link", { name: "Чат" })).toHaveAttribute("href", /\/patient\/chat$/);
  await page.screenshot({ path: testInfo.outputPath("profile-360.png") });
  expect(api.completion()).toEqual({
    expected_revision: 0,
    display_name: "Марина",
    reminder_time: "14:00",
    mood_palette: "green",
    story_mode: "text",
    text_size: "large",
  });
});

test("profile edits save the selected setting with the current revision", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  const api = await mockPatientPreferences(page, { completedAt: "2026-10-20T09:00:00Z" });
  await page.goto("/patient/profile");

  await expect(page.getByRole("heading", { name: "Профиль" })).toBeVisible();
  await page.getByRole("link", { name: /Как к вам обращаться/ }).click();
  await expect(page.getByRole("heading", { name: "Обращение" })).toBeVisible();
  await page.getByLabel("Имя").fill("Марина");
  await page.getByRole("button", { name: "Сохранить" }).click();

  await expect(page.getByRole("status")).toContainText("Настройка сохранена");
  expect(api.profileUpdate()).toEqual({ expected_revision: 0, display_name: "Марина" });
  expect(api.preferences.display_name).toBe("Марина");
});

test("cached profile opens offline without cached scale definitions", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  const api = await mockPatientPreferences(page, { completedAt: "2026-10-20T09:00:00Z" });
  await page.goto("/patient/profile");
  await expect(page.getByRole("heading", { name: "Профиль" })).toBeVisible();

  api.setOffline(true);
  await page.reload();

  await expect(page.getByRole("heading", { name: "Профиль" })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "Офлайн-режим" })).toBeVisible();
  await expect(page.getByRole("link", { name: /Как к вам обращаться/ })).toContainText(
    "Марина Викторовна",
  );
  await expect(page.getByRole("button", { name: "Сохранить" })).toHaveCount(0);
});

test("ordinary logout clears cached preferences while retaining a normal session exit", async ({
  page,
}) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await mockPatientPreferences(page, { completedAt: "2026-10-20T09:00:00Z" });
  await page.goto("/patient/profile");
  await expect(page.getByRole("heading", { name: "Профиль" })).toBeVisible();
  await expect.poll(() => hasCachedPreferences(page, PATIENT_ID)).toBe(true);

  await page.getByRole("button", { name: "Выйти" }).click();

  await expect(page.getByLabel("Электронная почта", { exact: true })).toBeVisible();
  await expect.poll(() => hasCachedPreferences(page, PATIENT_ID)).toBe(false);
});

async function hasCachedPreferences(page: Page, ownerId: string): Promise<boolean> {
  return page.evaluate(async (owner) => {
    const database = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open("clin-compass-preferences", 1);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    const request = database.transaction("profiles", "readonly").objectStore("profiles").get(owner);
    const record = await new Promise<unknown>((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    database.close();
    return record !== undefined;
  }, ownerId);
}

test("owner changes load the new patient's profile instead of the previous cache", async ({
  page,
}) => {
  await page.setViewportSize({ width: 360, height: 800 });
  let currentOwner = PATIENT_ID;
  let currentName = "Марина Викторовна";
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (pathname === "/api/v1/auth/me") {
      await route.fulfill(
        json(200, {
          id: currentOwner,
          email: "patient@example.invalid",
          role: "patient",
          full_name: currentName,
        }),
      );
      return;
    }
    if (pathname === "/api/v1/me/preferences") {
      await route.fulfill(
        json(200, {
          display_name: currentName,
          reminder_time: "21:00",
          mood_palette: "warm",
          story_mode: "voice",
          text_size: "normal",
          onboarding_completed_at: "2026-10-20T09:00:00Z",
          revision: 0,
          timezone: "Europe/Moscow",
          updated_at: null,
        }),
      );
      return;
    }
    if (pathname === "/api/v1/me/prescriptions") {
      await route.fulfill(json(200, []));
      return;
    }
    if (pathname === "/api/v1/me/consent") {
      await route.fulfill(
        json(200, { version: "consent-2026-10", given_at: "2026-10-25T08:00:00Z", text: null }),
      );
      return;
    }
    await route.fulfill(json(200, {}));
  });
  await page.goto("/patient/profile");
  await expect(page.getByRole("link", { name: /Как к вам обращаться/ })).toContainText(
    "Марина Викторовна",
  );

  currentOwner = "00000000-0000-4000-8000-000000000099";
  currentName = "Ирина Сергеевна";
  await page.reload();

  await expect(page.getByRole("link", { name: /Как к вам обращаться/ })).toContainText(
    "Ирина Сергеевна",
  );
  await expect(page.getByRole("link", { name: /Как к вам обращаться/ })).not.toContainText(
    "Марина Викторовна",
  );
});
