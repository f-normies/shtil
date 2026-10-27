import type { Schemas } from "@compass/api-client";
import { type BrowserContext, expect, type Page, test } from "@playwright/test";
import { mockSessionApi, user } from "./session-api-mock";

/**
 * Эскалация «Обязательно» (спека #56, раздел 6) на подменённом API: плашка и ход обработки у
 * ведущего врача, карточка заместителя, служебный кабинет администратора. Данные выдуманы.
 */

type Alert = Schemas["AlertOut"];

const ME = user("doctor");
const LEAD = "00000000-0000-4000-8000-0000000000d2";
const LEBEDEVA = "00000000-0000-4000-8000-0000000000c1";
const KOTOV = "00000000-0000-4000-8000-0000000000c2";
const NOW = new Date("2026-10-22T10:00:00+03:00");
const SINCE = "2026-10-22T09:00:00+03:00";
const NAMES = {
  [ME.id]: "Громов Андрей Николаевич",
  [LEAD]: "Соколова Ирина Петровна",
};

function alert(overrides: Partial<Alert>): Alert {
  return {
    id: "urgent",
    patient_id: LEBEDEVA,
    doctor_id: ME.id,
    origin_type: "rule",
    rule_id: "phq9-suicidal-thoughts",
    rule_version: 1,
    kind: "alert",
    level: "mandatory",
    title: "Мысли о смерти или самоповреждении",
    doctor_actions: ["Связаться с пациенткой сегодня"],
    sources: [
      { source: "kr", locator: "2.5", title: "Синтетические КР", url: "https://example.invalid" },
    ],
    occurrences: 1,
    created_at: SINCE,
    last_triggered_at: SINCE,
    delivered_at: SINCE,
    delivered_via: "ws",
    acknowledged_at: null,
    acknowledged_by: null,
    state: "new",
    snoozed_until: null,
    resolved_at: null,
    resolved_by: null,
    status: "delivered",
    triggers: [{ at: SINCE, origin_ref: "s", rule_version: 1, level: "mandatory", evidence: {} }],
    actions: [
      {
        at: "2026-10-22T09:15:00+03:00",
        actor_id: null,
        action: "escalate",
        decision_id: null,
        decision_text: null,
        reason: null,
        snoozed_until: null,
        step: 1,
        recipient_id: ME.id,
      },
    ],
    actors: NAMES,
    decision: null,
    urgent_since: SINCE,
    deputy_id: null,
    deputy_access: null,
    as_deputy: false,
    escalation_step: 1,
    ...overrides,
  };
}

/** Сигнал врача Соколовой, переданный мне как заместителю на шаге 2. */
const HANDED = alert({
  id: "handed",
  patient_id: KOTOV,
  doctor_id: LEAD,
  deputy_id: ME.id,
  as_deputy: true,
  escalation_step: 2,
  actions: [
    { ...alert({}).actions[0], recipient_id: LEAD } as Alert["actions"][number],
    {
      ...(alert({}).actions[0] as Alert["actions"][number]),
      at: "2026-10-22T09:30:00+03:00",
      step: 2,
      recipient_id: ME.id,
    },
  ],
  deputy_access: {
    doctor_id: ME.id,
    granted_at: "2026-10-22T09:30:00+03:00",
    expires_at: "2026-10-22T21:30:00+03:00",
    ended_at: null,
    end_reason: null,
  },
});

function patient(id: string, access: "lead" | "deputy" = "lead"): Schemas["PatientSummary"] {
  return {
    id,
    full_name: id === KOTOV ? "Котов Сергей Андреевич" : "Лебедева Марина Викторовна",
    email: access === "deputy" ? "" : "p@example.invalid",
    lead_doctor_id: id === KOTOV ? LEAD : ME.id,
    created_at: "2026-09-22T08:00:00Z",
    phone: "+79990000000",
    birth_date: "1990-03-01",
    sex: id === KOTOV ? "male" : "female",
    pregnant: null,
    breastfeeding: null,
    egfr: null,
    egfr_measured_on: null,
    child_pugh: null,
    access,
  };
}

async function mockDoctor(context: BrowserContext, alerts: Alert[]) {
  await context.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const byPatient = /^\/api\/v1\/patients\/([^/]+)(?:\/(.+))?$/.exec(path);
    const id = byPatient?.[1] ?? "";
    const json = (() => {
      if (path === "/api/v1/patients") return [patient(LEBEDEVA)];
      if (path === "/api/v1/alerts") return alerts;
      if (path === "/api/v1/messaging/unread") return [];
      if (path === "/api/v1/proposals/summary") return { pending: 0, decided_today: 0 };
      if (path === "/api/v1/alerts/decisions") {
        return {
          safety: [
            { id: "risk-not-high", text: "Риск не высокий", reason_hint: "", applies_to: [] },
          ],
          no_contact: "Не удалось связаться",
          therapy: [],
          referral_decline: [],
          snooze: { max_days: 14, presets_days: [3, 7, 14] },
        };
      }
      if (path === "/api/v1/alerts/thresholds") return [];
      if (!byPatient) return null;
      switch (byPatient[2]) {
        case undefined:
          return patient(id, id === KOTOV ? "deputy" : "lead");
        case "alerts":
          return alerts.filter((a) => a.patient_id === id);
        case "assessments":
          return { items: [], next_cursor: null };
        case "assessments/schedule":
          return { today: "2026-10-22", items: [] };
        case "diagnoses":
        case "prescriptions":
        case "medications":
        case "reactions":
        case "proposals":
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

async function open(page: Page, path: string, alerts: Alert[]) {
  await page.clock.setFixedTime(NOW);
  await page.setViewportSize({ width: 1280, height: 832 });
  await mockSessionApi(page.context(), "doctor");
  await mockDoctor(page.context(), alerts);
  await page.goto(path);
}

test("lead doctor sees how long the signal waits and the steps in its history", async ({
  page,
}, testInfo) => {
  await open(page, `/doctor/patients/${LEBEDEVA}/scales`, [alert({})]);

  const banner = page.getByRole("alert", { name: "Сигналы «Обязательно»" });
  await expect(banner).toContainText("не взят 15 мин");
  await page.screenshot({ path: testInfo.outputPath("escalation-banner-1280.png") });
  await banner.getByRole("link", { name: "Открыть сигнал" }).click();

  await expect(page.getByText("Не взят 15 мин — повтор")).toBeVisible();
});

test("deputy sees whose patient it is and only the overview and scales", async ({
  page,
}, testInfo) => {
  await open(page, "/doctor/alerts", [HANDED]);

  const row = page.getByRole("link", { name: /Замещение, ведущий врач — Соколова И. П./ });
  await expect(row).toBeVisible();
  await row.click();
  await expect(page.getByRole("button", { name: "Взять в работу" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Направить к психиатру" })).toHaveCount(0);
  await page.getByRole("link", { name: "Открыть карточку" }).click();

  await expect(page.getByText("Доступ по замещению до 21:30")).toBeVisible();
  const sections = page.getByRole("radiogroup", { name: "Разделы карточки" });
  await expect(sections.getByRole("radio")).toHaveCount(2);
  await expect(sections.getByRole("radio", { name: "Обзор" })).toBeChecked();
  await expect(sections.getByRole("radio", { name: "Шкалы" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Изменить телефон" })).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("escalation-deputy-card-1280.png") });
});

test("deputy can read external medications and reactions without therapy controls", async ({
  page,
}) => {
  await mockSessionApi(page.context(), "doctor");
  await mockDoctor(page.context(), [HANDED]);
  const medication: Schemas["MedicationOut"] = {
    id: "external",
    name: "Синтетин вне системы",
    inn: "Синтетин",
    registry_check: "registered",
    started_on: "2026-10-01",
    stopped_on: null,
    created_at: SINCE,
    entered_by: LEAD,
  };
  const report: Schemas["ReactionReportOut"] = {
    id: "reaction",
    prescription_id: "prescription",
    reported_at: SINCE,
    note: "Синтетическое описание реакции",
    reactions: [
      { id: "r", level: "desirable", title: "Синтетическая реакция", patient_title: "Реакция" },
    ],
  };
  await page
    .context()
    .route(`**/api/v1/patients/${KOTOV}/medications`, (route) =>
      route.fulfill({ json: [medication] }),
    );
  await page
    .context()
    .route(`**/api/v1/patients/${KOTOV}/reactions`, (route) => route.fulfill({ json: [report] }));
  await page.goto(`/doctor/patients/${KOTOV}`);

  await expect(page.getByText("Синтетин вне системы", { exact: true })).toBeVisible();
  await expect(page.getByText("Пациент пишет: «Синтетическое описание реакции»")).toBeVisible();
  await expect(page.getByRole("button", { name: "Добавить препарат" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Отменён сегодня" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Черновик извещения" })).toHaveCount(0);
});

test("deputy overview never requests adherence or shows its indicator", async ({ page }) => {
  await page.clock.setFixedTime(NOW);
  await mockSessionApi(page.context(), "doctor");
  await mockDoctor(page.context(), [HANDED]);
  const forbidden: string[] = [];
  await page.context().route("**/api/v1/patients/*/adherence**", async (route) => {
    forbidden.push(new URL(route.request().url()).pathname);
    await route.fulfill({
      status: 403,
      json: { status: 403, detail: "Adherence is outside deputy access" },
    });
  });
  await page.goto(`/doctor/patients/${KOTOV}`);
  await expect(page.getByText("Доступ по замещению до 21:30")).toBeVisible();
  await expect(page.getByText("Пациент ещё не заполнял шкалы.")).toBeVisible();
  await page.waitForLoadState("networkidle");
  expect(forbidden).toEqual([]);
  await expect(page.getByRole("link", { name: /^Приём/ })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Сводка с прошлого приёма" })).toHaveCount(0);
});

const DOCTORS: Schemas["AdminDoctorOut"][] = [
  { id: LEAD, full_name: "Соколова Ирина Петровна", deputy: null, no_deputy: true },
  { id: ME.id, full_name: "Громов Андрей Николаевич", deputy: null, no_deputy: true },
];

const ESCALATION: Schemas["EscalationOut"] = {
  alert_id: "0192aaaa-bbbb-7000-8000-000000000001",
  number: "0192aaaa",
  urgent_since: SINCE,
  doctor_id: LEAD,
  deputy_id: null,
  step: 3,
  steps: [
    { step: 1, at: "2026-10-22T09:15:00+03:00", recipient_id: LEAD },
    { step: 2, at: "2026-10-22T09:30:00+03:00", recipient_id: null },
    { step: 3, at: "2026-10-22T09:30:00+03:00", recipient_id: null },
  ],
  acknowledged_at: null,
  acknowledged_by: null,
  resolved_at: null,
  access: null,
  reviewed_at: null,
  reviewed_by: null,
  names: { [LEAD]: "Соколова Ирина Петровна" },
};

test("admin assigns a deputy and reviews an escalation", async ({ page }, testInfo) => {
  const posted: { path: string; body: unknown }[] = [];
  await page.clock.setFixedTime(NOW);
  await page.setViewportSize({ width: 1280, height: 900 });
  await mockSessionApi(page.context(), "admin");
  await page.context().route("**/api/v1/admin/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === "POST") {
      posted.push({ path, body: request.postDataJSON() });
      if (path.endsWith("/review")) {
        await route.fulfill({ status: 204, body: "" });
        return;
      }
      await route.fulfill({
        status: 409,
        json: { detail: "Период пересекается с назначением 20.10–26.10" },
      });
      return;
    }
    if (path === "/api/v1/admin/doctors") return route.fulfill({ json: DOCTORS });
    if (path === "/api/v1/admin/deputies") return route.fulfill({ json: [] });
    return route.fulfill({ json: [ESCALATION] });
  });
  await page.goto("/doctor/admin");

  await expect(page.getByText("У 2 врачей нет заместителя на сегодня")).toBeVisible();
  const row = page.getByRole("row", { name: /0192aaaa/ });
  await expect(row).toContainText("09:30 — сообщено администратору");
  await expect(row).not.toContainText("Лебедева");
  await page.screenshot({ path: testInfo.outputPath("escalation-admin-1280.png"), fullPage: true });

  await row.getByRole("button", { name: "Разобрано" }).click();
  await page.getByRole("combobox", { name: "Врач" }).selectOption(LEAD);
  await page.getByRole("combobox", { name: "Заместитель" }).selectOption(ME.id);
  await page.getByRole("textbox", { name: "С", exact: true }).fill("2026-10-22");
  await page.getByRole("textbox", { name: "По", exact: true }).fill("2026-10-28");
  await page.getByRole("textbox", { name: "Основание" }).fill("Приказ № 12 от 20.10.2026");
  await page.getByRole("button", { name: "Назначить" }).click();

  await expect(page.getByText("Период пересекается с назначением 20.10–26.10")).toBeVisible();
  expect(posted).toEqual([
    { path: `/api/v1/admin/escalations/${ESCALATION.alert_id}/review`, body: null },
    {
      path: "/api/v1/admin/deputies",
      body: {
        doctor_id: LEAD,
        deputy_id: ME.id,
        starts_on: "2026-10-22",
        ends_on: "2026-10-28",
        basis: "Приказ № 12 от 20.10.2026",
      },
    },
  ]);
});

test("admin catches an urgent escalation missed during a WebSocket disconnect", async ({
  page,
}) => {
  await mockSessionApi(page.context(), "admin");
  await page.clock.install({ time: NOW });
  let escalations: Schemas["EscalationOut"][] = [];
  await page.context().route("**/api/v1/admin/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = path.endsWith("/doctors")
      ? DOCTORS
      : path.endsWith("/deputies")
        ? []
        : escalations;
    await route.fulfill({ json });
  });
  const sockets: import("@playwright/test").WebSocketRoute[] = [];
  await page.routeWebSocket("**/api/v1/ws", (socket) => {
    sockets.push(socket);
    socket.send(JSON.stringify({ type: "ready" }));
  });
  await page.goto("/doctor/admin");
  await expect(page.getByText("Неразобранных эскалаций нет.")).toBeVisible();
  // StrictMode открывает пробное соединение и закрывает его при проверке эффекта.
  // Разрываем последнее соединение, которое обслуживает смонтированную страницу.
  const connected = sockets.at(-1);
  expect(connected).toBeDefined();
  await connected?.close();
  escalations = [ESCALATION];
  await page.clock.fastForward(5_001);

  await expect(page.getByRole("row", { name: /0192aaaa/ })).toBeVisible();
  const banner = page.getByRole("alert", { name: "Эскалация" });
  await expect(banner).toContainText("Срочный сигнал");
  await expect(banner).not.toContainText("Лебедева");
  await expect(banner).not.toContainText("Мысли о смерти");
});

test("admin page goes to login when the WebSocket session ends", async ({ page }) => {
  const api = await mockSessionApi(page.context(), "admin");
  const sessionEnded = api.endSessionOnSocket();
  await page.goto("/doctor/admin");
  await sessionEnded;

  await expect(page).toHaveURL(/\/doctor\/login$/);
  await expect(page.getByLabel("Электронная почта", { exact: true })).toBeVisible();
});

test("admin revokes a deputy only after confirming in the row", async ({ page }) => {
  const posted: string[] = [];
  const assignment: Schemas["DeputyOut"] = {
    id: "00000000-0000-4000-8000-0000000000a1",
    doctor_id: LEAD,
    doctor_name: "Соколова Ирина Петровна",
    deputy_id: ME.id,
    deputy_name: "Громов Андрей Николаевич",
    starts_on: "2026-10-20",
    ends_on: "2026-10-26",
    basis: "Приказ № 12 от 20.10.2026",
    created_at: "2026-10-20T09:00:00+03:00",
    revoked_at: null,
  };
  await mockSessionApi(page.context(), "admin");
  await page.context().route("**/api/v1/admin/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === "POST") {
      posted.push(path);
      return route.fulfill({ json: { ...assignment, revoked_at: "2026-10-22T10:00:00+03:00" } });
    }
    if (path === "/api/v1/admin/doctors") return route.fulfill({ json: DOCTORS });
    if (path === "/api/v1/admin/deputies") return route.fulfill({ json: [assignment] });
    return route.fulfill({ json: [] });
  });
  await page.goto("/doctor/admin");
  const row = page.getByRole("row", { name: /Приказ № 12/ });

  await row.getByRole("button", { name: "Отозвать" }).click();
  await expect(row).toContainText("Доступы заместителя к пациентам закроются");
  await row.getByRole("button", { name: "Отмена" }).click();
  await expect(row.getByRole("button", { name: "Отозвать" })).toBeVisible();
  expect(posted).toEqual([]);

  await row.getByRole("button", { name: "Отозвать" }).click();
  await row.getByRole("button", { name: "Да, отозвать" }).click();
  await expect.poll(() => posted).toEqual([`/api/v1/admin/deputies/${assignment.id}/revoke`]);
});

test("deputy sees the patient name in the banner and the signal list", async ({ page }) => {
  await open(page, `/doctor/patients/${LEBEDEVA}/scales`, [HANDED]);
  const banner = page.getByRole("alert", { name: "Сигналы «Обязательно»" });
  await expect(banner).toContainText("Котов Сергей Андреевич");

  await page.goto("/doctor/alerts");
  await expect(page.getByRole("link", { name: /Замещение/ })).toContainText("Котов");
});
