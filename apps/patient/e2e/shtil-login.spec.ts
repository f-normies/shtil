import { expect, type Locator, type Page, test } from "@playwright/test";
import { mockSessionApi } from "./session-api-mock";

const EVIDENCE_DIR =
  "../../.superpowers/sdd/2026-10-21-20-shtil-design-system-design/evidence/login";

async function saveEvidence(page: Page, fileName: string) {
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  await page.screenshot({ path: `${EVIDENCE_DIR}/${fileName}`, fullPage: true });
}

async function readMarkAttributes(mark: Locator) {
  return mark.evaluate(async (image: HTMLImageElement) => {
    const source = image.currentSrc;
    let svgSource: string;
    if (source.startsWith("data:")) {
      const comma = source.indexOf(",");
      const metadata = source.slice(0, comma);
      const payload = source.slice(comma + 1);
      svgSource = metadata.endsWith(";base64") ? atob(payload) : decodeURIComponent(payload);
    } else {
      svgSource = await fetch(source).then((response) => response.text());
    }

    const svg = new DOMParser().parseFromString(svgSource, "image/svg+xml");
    return {
      viewBox: svg.documentElement.getAttribute("viewBox"),
      fills: Array.from(svg.querySelectorAll("[fill]")).map((element) =>
        element.getAttribute("fill")?.toLowerCase(),
      ),
    };
  });
}

async function verticalGapBetween(bottom: Locator, top: Locator) {
  const [bottomBox, topBox] = await Promise.all([bottom.boundingBox(), top.boundingBox()]);
  if (!bottomBox || !topBox) throw new Error("Expected both login elements to have a box");
  return topBox.y - (bottomBox.y + bottomBox.height);
}

test("doctor login uses the doctor mode, 400px card, brand, and working password toggle", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 832 });
  await mockSessionApi(page.context(), null);
  await page.goto("/doctor/login");

  await expect(page.locator("html")).toHaveAttribute("data-tsvet", "vrach");
  await expect(page.locator("html")).toHaveAttribute("data-forma", "vrach");
  await expect(page.getByRole("heading", { name: "Штиль", exact: true })).toBeVisible();
  await expect(page.getByText("Кабинет врача", { exact: true })).toBeVisible();
  const doctorMark = page.locator(".ui-brand-mark");
  await expect(doctorMark).toBeVisible();
  await expect
    .poll(() => doctorMark.evaluate((image: HTMLImageElement) => image.naturalWidth))
    .toBe(64);
  const doctorMarkAttributes = await readMarkAttributes(doctorMark);
  expect(doctorMarkAttributes.viewBox).toBe("0 0 64 64");
  expect(doctorMarkAttributes.fills).toContain("#1f5f77");
  await expect(page.locator("html")).toHaveCSS("background-color", "rgb(242, 245, 247)");
  await expect(page.locator("body")).toHaveCSS("background-color", "rgb(242, 245, 247)");
  await expect(page.locator("body")).toHaveCSS("font-family", /Onest/);

  const card = page.locator(".doctor-login__card");
  await expect(card).toHaveCSS("width", "400px");
  expect((await card.boundingBox())?.x).toBe(440);
  const doctorEye = await page.locator(".ui-field__password-toggle img").boundingBox();
  expect(doctorEye).not.toBeNull();
  expect((doctorEye?.x ?? 0) + (doctorEye?.width ?? 0) / 2).toBe(776.5);
  await expect(page.getByLabel("Электронная почта", { exact: true })).toHaveValue("");
  await expect(page.getByLabel("Пароль", { exact: true })).toHaveValue("");
  await expect(page.getByText("Электронная почта", { exact: true })).toHaveCSS(
    "font-weight",
    "400",
  );
  await expect(page.getByText("Пароль", { exact: true })).toHaveCSS("font-weight", "400");

  // Восстановления пароля нет: кнопка «Забыли пароль?» не показывается (кадр 01в).
  await expect(page.getByRole("button", { name: "Забыли пароль?", exact: true })).toHaveCount(0);
  // Фокус при открытии — в поле почты (кадр 01).
  await expect(page.getByLabel("Электронная почта", { exact: true })).toBeFocused();
  await saveEvidence(page, "doctor-login.png");

  await page.getByLabel("Электронная почта", { exact: true }).fill("i.sokolova@clinic.example");
  const password = page.getByLabel("Пароль", { exact: true });
  await password.fill("spokoyno-2026");
  await page.getByRole("button", { name: "Показать пароль", exact: true }).click();
  await expect(password).toHaveAttribute("type", "text");
  await expect(password).toHaveValue("spokoyno-2026");
  await page.getByRole("button", { name: "Скрыть пароль", exact: true }).click();
  await expect(password).toHaveAttribute("type", "password");
  await expect(password).toHaveValue("spokoyno-2026");
  await page.getByRole("button", { name: "Показать пароль", exact: true }).click();
  await saveEvidence(page, "doctor-password-visible.png");
});

test("patient login uses the cool patient mode and 360px layout", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 780 });
  await mockSessionApi(page.context(), null);
  await page.goto("/patient/login");

  await expect(page.locator("html")).toHaveAttribute("data-tsvet", "patsient-prokhladnaya");
  await expect(page.locator("html")).toHaveAttribute("data-forma", "patsient");
  await expect(page.getByText("Штиль", { exact: true })).toBeVisible();
  const patientMark = page.locator(".ui-brand-mark");
  await expect(patientMark).toBeVisible();
  await expect
    .poll(() => patientMark.evaluate((image: HTMLImageElement) => image.naturalWidth))
    .toBe(64);
  const patientMarkAttributes = await readMarkAttributes(patientMark);
  expect(patientMarkAttributes.viewBox).toBe("0 0 64 64");
  expect(patientMarkAttributes.fills).toContain("#f2a57c");
  await expect(page.getByRole("heading", { name: "С возвращением", exact: true })).toBeVisible();
  await expect(page.getByText("Войдите, чтобы сделать отметку и написать врачу.")).toBeVisible();
  await expect(page.locator("html")).toHaveCSS("background-color", "rgb(245, 250, 248)");
  await expect(page.locator("body")).toHaveCSS("background-color", "rgb(245, 250, 248)");
  await expect(page.getByLabel("Электронная почта", { exact: true })).toHaveValue("");
  await expect(page.getByLabel("Пароль", { exact: true })).toHaveValue("");
  const emailHint = page.getByText("Адрес, на который пришло приглашение", { exact: true });
  const passwordLabel = page.getByText("Пароль", { exact: true });
  await expect(emailHint).toBeVisible();
  expect(await verticalGapBetween(emailHint, passwordLabel)).toBeGreaterThanOrEqual(16);
  await expect(
    page.getByText("Нет аккаунта? Попросите врача прислать приглашение.", { exact: true }),
  ).toBeVisible();

  const submit = page.getByRole("button", { name: "Войти", exact: true });
  await expect(submit).toHaveCSS("min-height", "56px");
  await expect(submit).toHaveCSS("border-radius", "999px");
  expect(
    await verticalGapBetween(page.getByLabel("Пароль", { exact: true }), submit),
  ).toBeGreaterThanOrEqual(28);
  await saveEvidence(page, "patient-login.png");

  const password = page.getByLabel("Пароль", { exact: true });
  await password.fill("synthetic-password");
  await page.getByRole("button", { name: "Показать пароль", exact: true }).click();
  await expect(password).toHaveAttribute("type", "text");
  await expect(password).toHaveValue("synthetic-password");
});

test("patient login fields keep the source geometry without an operating-system bar", async ({
  page,
}) => {
  await page.setViewportSize({ width: 360, height: 780 });
  await mockSessionApi(page.context(), null);
  await page.goto("/patient/login");

  const email = await page.getByLabel("Электронная почта", { exact: true }).evaluate((input) => {
    const bounds = input.closest(".ui-field__control")?.getBoundingClientRect();
    if (!bounds) throw new Error("Email control is missing");
    return {
      x: bounds.x,
      y: bounds.y,
      width: bounds.width,
      height: bounds.height,
      paddingLeft: getComputedStyle(input).paddingLeft,
      paddingRight: getComputedStyle(input).paddingRight,
    };
  });
  const password = await page.getByLabel("Пароль", { exact: true }).evaluate((input) => {
    const bounds = input.closest(".ui-field__control")?.getBoundingClientRect();
    if (!bounds) throw new Error("Password control is missing");
    return { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height };
  });
  const submit = await page.getByRole("button", { name: "Войти", exact: true }).boundingBox();

  expect(email).toEqual({
    x: 20,
    y: 260,
    width: 320,
    height: 58,
    paddingLeft: "16px",
    paddingRight: "16px",
  });
  expect(password).toEqual({ x: 20, y: 384, width: 320, height: 58 });
  expect(submit).toEqual({ x: 20, y: 470, width: 320, height: 56 });
});

test("doctor login keeps credentials after 401 and allows a successful retry", async ({ page }) => {
  const api = await mockSessionApi(page.context(), null);
  api.setLoginRole("doctor");
  await page.goto("/doctor/login");

  const email = page.getByLabel("Электронная почта", { exact: true });
  const password = page.getByLabel("Пароль", { exact: true });
  await email.fill("wrong@example.invalid");
  await password.fill("invalid-password");
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Почта или пароль не подошли");
  await expect(email).toHaveValue("wrong@example.invalid");
  await expect(password).toHaveValue("invalid-password");
  await saveEvidence(page, "doctor-error-401.png");

  await email.fill("doctor@example.invalid");
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(page).toHaveURL(/\/doctor\/$/);
  await expect(page.getByRole("navigation", { name: "Разделы кабинета" })).toBeVisible();
  expect(api.calls.filter(({ path }) => path === "/api/v1/auth/login")).toHaveLength(2);
});

test("patient login preserves fields through 429 and network errors, then retries", async ({
  page,
}) => {
  const api = await mockSessionApi(page.context(), null);
  api.setLoginRole("patient");
  const attempts: unknown[] = [];
  page.on("request", (request) => {
    if (request.url().endsWith("/api/v1/auth/login")) attempts.push(request.postDataJSON());
  });
  await page.goto("/patient/login");

  const email = page.getByLabel("Электронная почта", { exact: true });
  const password = page.getByLabel("Пароль", { exact: true });
  await email.fill("patient@example.invalid");
  await password.fill("synthetic-password");

  await page.route("**/api/v1/auth/login", (route) =>
    route.fulfill({
      status: 429,
      contentType: "application/json",
      body: JSON.stringify({ status: 429, retry_after: 30 }),
    }),
  );
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("через несколько секунд");
  await expect(email).toHaveValue("patient@example.invalid");
  await expect(password).toHaveValue("synthetic-password");
  await saveEvidence(page, "patient-error-429.png");
  await page.unroute("**/api/v1/auth/login");

  await page.route("**/api/v1/auth/login", (route) => route.abort());
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Не удалось войти");
  await expect(email).toHaveValue("patient@example.invalid");
  await expect(password).toHaveValue("synthetic-password");
  await page.unroute("**/api/v1/auth/login");

  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(page).toHaveURL(/\/patient\/$/);
  await expect(
    page.getByRole("heading", { name: "Здравствуйте, Синтетический patient" }),
  ).toBeVisible();
  expect(attempts).toHaveLength(3);
  expect(api.calls.filter(({ path }) => path === "/api/v1/auth/login")).toHaveLength(1);
});
