import type { Schemas } from "@compass/api-client";
import { type BrowserContext, expect, type Page } from "@playwright/test";
import { mockSessionApi, user } from "./session-api-mock";

/** Экран «Сигналы» кабинета врача (спека #95) на подменённом API. Все данные выдуманы. */

export type Alert = Schemas["AlertOut"];

export const DOCTOR = user("doctor");
export const LEBEDEVA = "00000000-0000-4000-8000-0000000000c1";
export const KOTOV = "00000000-0000-4000-8000-0000000000c2";
export const SOURCE = {
  source: "kr",
  locator: "раздел 2.5",
  title: "Синтетические рекомендации",
  url: "https://example.invalid/kr",
};
export const BAND = (label: string) => ({
  code: label,
  label,
  source: {
    id: "v",
    locator: "1",
    title: "v",
    url: "https://example.invalid/v",
    supplementary: false,
  },
});

export function alert(overrides: Partial<Alert>): Alert {
  return {
    as_deputy: false,
    escalation_step: null,
    id: "a",
    patient_id: LEBEDEVA,
    doctor_id: DOCTOR.id,
    origin_type: "rule",
    rule_id: "phq9-suicidal-thoughts",
    rule_version: 2,
    kind: "alert",
    level: "mandatory",
    title: "Мысли о смерти или самоповреждении",
    doctor_actions: ["Связаться с пациентом сегодня", "Оценить суицидальный риск"],
    sources: [SOURCE],
    occurrences: 1,
    created_at: "2026-10-22T06:14:00Z",
    last_triggered_at: "2026-10-22T06:14:00Z",
    delivered_at: "2026-10-22T06:14:02Z",
    delivered_via: "ws",
    acknowledged_at: null,
    acknowledged_by: null,
    state: "new",
    snoozed_until: null,
    resolved_at: null,
    resolved_by: null,
    status: "delivered",
    triggers: [
      {
        at: "2026-10-22T06:14:00Z",
        origin_ref: "s",
        rule_version: 2,
        level: "mandatory",
        evidence: { "phq-9": { "phq9-9": 1 } },
      },
    ],
    actions: [],
    actors: {},
    decision: null,
    ...overrides,
  };
}

export const MANDATORY = alert({ id: "mandatory" });
export const DESIRABLE = alert({
  id: "desirable",
  patient_id: KOTOV,
  rule_id: "routing-depression-probable",
  kind: "routing",
  level: "desirable",
  title: "Вероятная депрессия лёгкой или средней тяжести",
  doctor_actions: ["Обсудить консультацию психиатра на ближайшем приёме"],
  last_triggered_at: "2026-10-21T15:20:00Z",
  triggers: [
    {
      at: "2026-10-21T15:20:00Z",
      origin_ref: "s",
      rule_version: 1,
      level: "desirable",
      evidence: { "phq-9": { total: 16 } },
    },
  ],
});

export const PHQ9 = {
  scale: {
    id: "phq-9",
    title: "PHQ-9",
    instruction: "За последние 2 недели",
    version: 1,
    purpose: "screening",
    respondent: "patient",
    status: "reviewed",
    items: [
      {
        id: "phq9-9",
        text: "Мысли о том, что лучше было бы умереть",
        options: [
          { value: 0, text: "Совсем нет" },
          { value: 1, text: "Несколько дней" },
          { value: 2, text: "Больше половины дней" },
          { value: 3, text: "Почти каждый день" },
        ],
      },
    ],
    license: { terms: "t", allowed: true, source: { source: "v", locator: "1" } },
    scores: [
      {
        id: "total",
        items: ["phq9-9"],
        bands: [
          { min: 0, max: 9, code: "mild", label: "Лёгкая", source: { source: "v", locator: "1" } },
          { min: 10, max: 27, code: "high", label: "Выше", source: { source: "v", locator: "1" } },
        ],
      },
    ],
    validation: [{ source: "v", locator: "1" }],
  },
  sources: [],
};

export const THRESHOLDS = [
  {
    rule_id: "phq9-suicidal-thoughts",
    kind: "alert",
    level: "mandatory",
    item: "phq9-9",
    score: null,
    op: ">=",
    value: 1,
  },
  {
    rule_id: "routing-depression-probable",
    kind: "routing",
    level: "desirable",
    item: null,
    score: "total",
    op: ">=",
    value: 10,
  },
  {
    rule_id: "routing-depression-severe",
    kind: "routing",
    level: "mandatory",
    item: null,
    score: "total",
    op: ">=",
    value: 20,
  },
];

export const DECISIONS = {
  safety: [
    {
      id: "risk-not-high",
      text: "Риск не высокий: наблюдение продолжается",
      reason_hint: "план наблюдения",
      applies_to: [],
    },
  ],
  no_contact: "Не удалось связаться",
  therapy: [],
  referral_decline: [
    { id: "already-under-psychiatrist", text: "Пациент уже наблюдается у психиатра" },
    { id: "patient-declined", text: "Пациент отказался от консультации" },
    { id: "other", text: "Другое" },
  ],
  snooze: { max_days: 14, presets_days: [3, 7, 14] },
};

export function assessment(id: string, at: string, total: number, band: string) {
  return {
    id,
    submission_id: id,
    patient_id: KOTOV,
    scale_id: "phq-9",
    scale_version: 1,
    scale_title: "PHQ-9",
    completed_at: at,
    received_at: at,
    answers: {},
    scores: { total: { value: total, band: BAND(band) } },
    sources: [],
  };
}

/** Подмена API экрана: списки, данные решений, пациенты и шкала. Тело действий записывается. */
export async function mockSignals(context: BrowserContext, extra: Alert[] = []) {
  const posted: { path: string; body: unknown }[] = [];
  await context.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (request.method() === "POST" && path.endsWith("/referral")) {
      posted.push({ path, body: request.postDataJSON() });
      await route.fulfill({ json: { proposal: null, alerts: [] } });
      return;
    }
    if (request.method() === "POST" && path.startsWith("/api/v1/alerts/")) {
      posted.push({ path, body: request.postDataJSON() });
      const target = path.includes("mandatory") ? MANDATORY : DESIRABLE;
      await route.fulfill({
        json: { ...target, state: path.endsWith("snooze") ? "snoozed" : "resolved" },
      });
      return;
    }
    const json = (() => {
      if (path === "/api/v1/alerts") {
        return url.searchParams.get("status") === "resolved"
          ? []
          : [DESIRABLE, MANDATORY, ...extra];
      }
      if (path === "/api/v1/alerts/decisions") return DECISIONS;
      if (path === "/api/v1/alerts/thresholds") return THRESHOLDS;
      if (path === "/api/v1/assessments/scales/phq-9") return PHQ9;
      if (path === "/api/v1/patients") {
        return [
          { id: LEBEDEVA, full_name: "Лебедева Марина Викторовна" },
          { id: KOTOV, full_name: "Котов Сергей Андреевич" },
        ];
      }
      if (path.endsWith("/assessments")) {
        return {
          items: [
            assessment("p1", "2026-10-04T09:00:00Z", 12, "Умеренная"),
            assessment("p2", "2026-10-18T15:15:00Z", 16, "Умеренно тяжёлая"),
          ],
          next_cursor: null,
        };
      }
      const patient = /^\/api\/v1\/patients\/([^/]+)$/.exec(path)?.[1];
      if (patient) {
        return {
          id: patient,
          full_name: patient === KOTOV ? "Котов Сергей Андреевич" : "Лебедева Марина Викторовна",
          email: "p@example.invalid",
          lead_doctor_id: DOCTOR.id,
          created_at: "2026-09-22T09:00:00Z",
          phone: "+79990000000",
          birth_date: "1992-05-01",
          sex: patient === KOTOV ? "male" : "female",
        };
      }
      return null;
    })();
    if (json === null) {
      await route.fallback();
      return;
    }
    await route.fulfill({ json });
  });
  return posted;
}

export async function openSignals(
  page: Page,
  extra: Alert[] = [],
  setup?: (context: BrowserContext) => Promise<void>,
) {
  await page.setViewportSize({ width: 1280, height: 900 });
  await mockSessionApi(page.context(), "doctor");
  const posted = await mockSignals(page.context(), extra);
  // Подмены, зарегистрированные позже, главнее: например, свой WebSocket.
  await setup?.(page.context());
  await page.goto("/doctor/alerts");
  await expect(page.getByRole("heading", { name: "Сигналы", level: 1 })).toBeVisible();
  return posted;
}
