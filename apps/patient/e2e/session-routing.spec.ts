import type { Page } from "@playwright/test";
import { expect, test } from "@playwright/test";
import { PATIENT_A } from "./assessment-fixtures";
import { mockSessionApi, type Role } from "./session-api-mock";

/** The landmark each role sees on its own start page. */
function homeLandmark(page: Page, role: Role) {
  if (role === "doctor") return page.getByRole("navigation", { name: "Разделы кабинета" });
  if (role === "admin") return page.getByRole("heading", { name: "Служебный кабинет" });
  return page.getByRole("heading", { name: "Здравствуйте, Синтетический patient" });
}

/** Paths of the HTML documents the browser loaded: a second one means a full page reload. */
function recordDocuments(page: Page): string[] {
  const documents: string[] = [];
  page.on("request", (request) => {
    if (request.resourceType() === "document") documents.push(new URL(request.url()).pathname);
  });
  return documents;
}

function recordDoctorSockets(page: Page): string[] {
  const sockets: string[] = [];
  page.on("websocket", (socket) => {
    if (new URL(socket.url()).pathname === "/api/v1/ws") sockets.push(socket.url());
  });
  return sockets;
}

test("admin opens only the service page and can end the session", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  const api = await mockSessionApi(page.context(), "admin");
  await page.goto("/doctor/admin");

  await expect(page.getByRole("heading", { name: "Служебный кабинет" })).toBeVisible();
  await expect(page.getByText("Синтетический admin")).toBeVisible();
  await expect(page.getByRole("button", { name: "Выйти" })).toBeVisible();
  // Служебный кабинет читает только свои ручки: заместителей и эскалации (#56).
  expect(
    api.calls.filter(
      ({ path }) => path !== "/api/v1/auth/me" && !path.startsWith("/api/v1/admin/"),
    ),
  ).toEqual([]);

  await page.getByRole("button", { name: "Выйти" }).click();
  await expect(page).toHaveURL(/\/doctor\/login$/);
  await expect(page.getByText("Кабинет врача")).toBeVisible();
});

for (const role of ["doctor", "admin", "patient"] as const) {
  test(`successful login routes ${role} to its own home`, async ({ page }) => {
    const api = await mockSessionApi(page.context(), null);
    api.setLoginRole(role);
    await page.goto("/doctor/login");
    await page.getByLabel("Электронная почта", { exact: true }).fill(`${role}@example.invalid`);
    await page.getByLabel("Пароль", { exact: true }).fill("synthetic-password");
    await page.getByRole("button", { name: "Войти" }).click();

    if (role === "doctor") {
      await expect(page).toHaveURL(/\/doctor\/$/);
    } else if (role === "admin") {
      await expect(page).toHaveURL(/\/doctor\/admin$/);
    } else {
      await expect(page).toHaveURL(/\/patient\/$/);
    }
    await expect(homeLandmark(page, role)).toBeVisible();
  });
}

test("an unauthenticated visitor opening the service page is sent to doctor login", async ({
  page,
}) => {
  await mockSessionApi(page.context(), null);
  await page.goto("/doctor/admin");

  await expect(page).toHaveURL(/\/doctor\/login$/);
  await expect(page.getByLabel("Электронная почта", { exact: true })).toBeVisible();
});

// Each role opens another cabinet directly, including its login page. A page of the same
// application opens without a reload; another application needs one full page load.
const FOREIGN_PAGES = [
  { role: "admin", open: "/patient/", home: "/doctor/admin", sameApp: false },
  { role: "admin", open: "/patient/login", home: "/doctor/admin", sameApp: false },
  { role: "admin", open: "/doctor/", home: "/doctor/admin", sameApp: true },
  { role: "doctor", open: "/doctor/admin", home: "/doctor/", sameApp: true },
  { role: "doctor", open: "/patient/", home: "/doctor/", sameApp: false },
  { role: "patient", open: "/doctor/admin", home: "/patient/", sameApp: false },
  { role: "patient", open: "/doctor/login", home: "/patient/", sameApp: false },
] as const;

for (const { role, open, home, sameApp } of FOREIGN_PAGES) {
  test(`${role} opening ${open} lands on ${home} without foreign requests`, async ({ page }) => {
    await page.setViewportSize(
      role === "patient" ? { width: 360, height: 800 } : { width: 1280, height: 900 },
    );
    const api = await mockSessionApi(page.context(), role);
    const documents = recordDocuments(page);
    const sockets = recordDoctorSockets(page);
    await page.goto(open);

    await expect(page).toHaveURL(new RegExp(`${home}$`));
    await expect(homeLandmark(page, role)).toBeVisible();
    expect(documents).toEqual(sameApp ? [open] : [open, home]);
    expect(
      api.calls.filter(({ path, pagePath }) => pagePath === open && path !== "/api/v1/auth/me"),
    ).toEqual([]);
    if (role !== "doctor") {
      const patientAndAlertCalls = api.calls.filter(
        ({ path }) => path.startsWith("/api/v1/patients") || path.startsWith("/api/v1/alerts"),
      );
      expect(patientAndAlertCalls).toEqual(
        role === "patient"
          ? [
              {
                method: "GET",
                pagePath: home,
                path: `/api/v1/patients/${PATIENT_A}/messages`,
              },
            ]
          : [],
      );
      // Администратор слушает эскалации (#56); пациенту сокет кабинета врача не нужен.
      if (role === "patient") expect(sockets).toEqual([]);
    }
    if (role !== "patient") {
      expect(api.calls.filter(({ path }) => path.startsWith("/api/v1/assessments"))).toEqual([]);
    }
  });
}

test("the patient route sends a signed-in doctor directly to the doctor app", async ({ page }) => {
  const api = await mockSessionApi(page.context(), "doctor");
  await page.goto("/patient/login");

  await expect(page).toHaveURL(/\/doctor\/$/);
  await expect(page.getByRole("navigation", { name: "Разделы кабинета" })).toBeVisible();
  expect(
    api.calls.some(({ path, pagePath }) => path === "/api/v1/auth/me" && pagePath === "/patient/"),
  ).toBe(false);
});

test("a patient reaching the doctor app goes to the patient home without doctor requests", async ({
  page,
}) => {
  await page.setViewportSize({ width: 360, height: 800 });
  const api = await mockSessionApi(page.context(), "patient");
  const sockets = recordDoctorSockets(page);
  await page.goto("/doctor/");

  await expect(page).toHaveURL(/\/patient\/$/);
  await expect(homeLandmark(page, "patient")).toBeVisible();
  expect(api.calls.filter(({ path }) => path === "/api/v1/patients")).toEqual([]);
  expect(sockets).toEqual([]);
});
