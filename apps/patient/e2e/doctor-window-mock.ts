import type { Schemas } from "@compass/api-client";
import type { BrowserContext, Page } from "@playwright/test";
import { mockSessionApi, user } from "./session-api-mock";

/**
 * Окно кабинета врача v4 (спека #13, раздел 10; #99): «Пациенты» и «Обзор» карточки
 * на подменённом API. Все пациенты и данные выдуманы.
 */

export type Alert = Schemas["AlertOut"];
export type Proposal = Schemas["ProposalOut"];

export const DOCTOR = user("doctor");
export const LEBEDEVA = "00000000-0000-4000-8000-0000000000c1";
export const KOTOV = "00000000-0000-4000-8000-0000000000c2";
export const ERSHOV = "00000000-0000-4000-8000-0000000000c3";
export const ORLOVA = "00000000-0000-4000-8000-0000000000c4";
export const FROLOV = "00000000-0000-4000-8000-0000000000c5";
export const NOW = new Date("2026-10-19T12:00:00+03:00");
export const SOURCE_REF = { source: "v", locator: "1" };

export const PATIENTS: [string, string, string][] = [
  [FROLOV, "Фролов Дмитрий Олегович", "2026-10-17T08:00:00Z"],
  [ORLOVA, "Орлова Наталья Петровна", "2026-09-01T08:00:00Z"],
  [ERSHOV, "Ершов Павел Ильич", "2026-09-10T08:00:00Z"],
  [KOTOV, "Котов Сергей Андреевич", "2026-09-15T08:00:00Z"],
  [LEBEDEVA, "Лебедева Марина Викторовна", "2026-09-22T08:00:00Z"],
];

export function patient(id: string): Schemas["PatientSummary"] {
  const [, full_name = "", created_at = ""] = PATIENTS.find(([p]) => p === id) ?? [];
  return {
    access: "lead",
    id,
    full_name,
    created_at,
    email: "p@example.invalid",
    phone: id === LEBEDEVA ? "+79990000000" : null,
    lead_doctor_id: DOCTOR.id,
    birth_date: id === LEBEDEVA ? "1992-05-01" : null,
    sex: null,
    pregnant: null,
    breastfeeding: null,
    child_pugh: null,
    egfr: null,
    egfr_measured_on: null,
  };
}

export function alert(overrides: Partial<Alert>): Alert {
  return {
    as_deputy: false,
    escalation_step: null,
    id: "a",
    patient_id: LEBEDEVA,
    doctor_id: DOCTOR.id,
    origin_type: "rule",
    rule_id: "phq9-suicidal-thoughts",
    rule_version: 1,
    kind: "alert",
    level: "mandatory",
    title: "Мысли о смерти или самоповреждении",
    doctor_actions: ["Связаться с пациенткой сегодня, затем оценить риск по C-SSRS."],
    sources: [
      {
        source: "kr",
        locator: "раздел 2.5",
        title: "Синтетические рекомендации",
        url: "https://example.invalid/kr",
      },
    ],
    occurrences: 1,
    created_at: "2026-10-19T06:14:00Z",
    last_triggered_at: "2026-10-19T06:14:00Z",
    delivered_at: "2026-10-19T06:14:02Z",
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
        at: "2026-10-19T06:14:00Z",
        origin_ref: "s",
        rule_version: 1,
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

export const ALERTS = [
  alert({ id: "mandatory" }),
  alert({
    id: "desirable",
    patient_id: KOTOV,
    level: "desirable",
    state: "in_work",
    acknowledged_at: "2026-10-18T10:00:00Z",
    acknowledged_by: DOCTOR.id,
    title: "PHQ-9 вырос с 12 до 16",
    last_triggered_at: "2026-10-18T09:00:00Z",
  }),
  alert({
    id: "snoozed",
    patient_id: ORLOVA,
    level: "desirable",
    state: "snoozed",
    snoozed_until: "2026-10-25",
    title: "Отложенный сигнал",
  }),
];

export function proposal(id: string, patientId: string, at: string): Proposal {
  return {
    id,
    patient_id: patientId,
    kind: "freetext.finding",
    payload: { category: "symptom", quote: "плохо сплю" },
    origin_type: "ai_run",
    origin_ref: "run",
    status: "proposed",
    created_at: at,
    decided_by: null,
    decided_at: null,
    decision_comment: null,
  };
}

export const PROPOSALS: Proposal[] = [
  proposal("p1", ERSHOV, "2026-10-19T05:30:00Z"),
  proposal("p2", ERSHOV, "2026-10-18T05:30:00Z"),
  proposal("p3", LEBEDEVA, "2026-10-18T18:30:00Z"),
];

export function band(label: string) {
  return {
    code: label,
    label,
    source: {
      id: "v",
      locator: "1",
      title: "v",
      url: "https://example.invalid/v",
      supplementary: false,
    },
  };
}

export function result(
  patientId: string,
  scale: "phq-9" | "gad-7",
  at: string,
  value: number,
  label: string,
) {
  return {
    id: `${patientId}-${scale}-${at}`,
    submission_id: at,
    patient_id: patientId,
    scale_id: scale,
    scale_version: 1,
    scale_title: scale === "phq-9" ? "Опросник здоровья пациента (PHQ-9)" : "Шкала тревоги (GAD-7)",
    completed_at: at,
    received_at: at,
    answers: Object.fromEntries(
      Array.from({ length: scale === "phq-9" ? 9 : 7 }, (_, i) => [
        `${scale.replace("-", "")}-${i + 1}`,
        value > 10 ? 1 : 0,
      ]),
    ),
    scores: { total: { value, band: band(label) } },
    sources: [],
    recorded_by: null,
  };
}

export const RESULTS = [
  result(LEBEDEVA, "phq-9", "2026-10-19T06:12:00Z", 6, "Лёгкая депрессия"),
  result(LEBEDEVA, "gad-7", "2026-10-19T06:12:00Z", 9, "Лёгкая тревога"),
  result(LEBEDEVA, "gad-7", "2026-10-12T06:12:00Z", 12, "Умеренная тревога"),
  result(LEBEDEVA, "phq-9", "2026-10-05T06:12:00Z", 8, "Лёгкая депрессия"),
  result(LEBEDEVA, "gad-7", "2026-10-05T06:12:00Z", 14, "Умеренная тревога"),
  result(ORLOVA, "phq-9", "2026-10-18T06:12:00Z", 7, "Лёгкая депрессия"),
  result(ORLOVA, "gad-7", "2026-10-18T06:12:00Z", 8, "Лёгкая тревога"),
];

export function definition(id: "phq-9" | "gad-7") {
  const bands =
    id === "phq-9"
      ? [
          [0, 4, "Минимальная депрессия или её нет"],
          [5, 9, "Лёгкая депрессия"],
          [10, 14, "Умеренная депрессия"],
          [15, 19, "Умеренно тяжёлая депрессия"],
          [20, 27, "Тяжёлая депрессия"],
        ]
      : [
          [0, 4, "Минимальная тревога"],
          [5, 9, "Лёгкая тревога"],
          [10, 14, "Умеренная тревога"],
          [15, 21, "Тяжёлая тревога"],
        ];
  return {
    scale: {
      id,
      title: id === "phq-9" ? "Опросник здоровья пациента (PHQ-9)" : "Шкала тревоги (GAD-7)",
      instruction: "За последние 2 недели",
      version: 1,
      purpose: "screening",
      respondent: "patient",
      status: "reviewed",
      items: Array.from({ length: id === "phq-9" ? 9 : 7 }, (_, i) => ({
        id: `${id.replace("-", "")}-${i + 1}`,
        text: `Пункт ${i + 1}`,
        options: [
          { value: 0, text: "Совсем нет" },
          { value: 1, text: "Несколько дней" },
        ],
      })),
      license: { terms: "t", allowed: true, source: SOURCE_REF },
      scores: [
        {
          id: "total",
          items: [],
          bands: bands.map(([min, max, label]) => ({
            min,
            max,
            code: label,
            label,
            source: SOURCE_REF,
          })),
        },
      ],
      validation: [SOURCE_REF],
    },
    sources: [],
  };
}

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
  referral_decline: [],
  snooze: { max_days: 14, presets_days: [3, 7, 14] },
};

/** Расписание шкал (#111): у PHQ-9 пропущена плановая дата 12 октября. */
/** Опросы пациента (#180): PHQ-9 раз в неделю по выбору врача, GAD-7 по умолчанию. */
const survey = (scale_id: string, period_days: number, updated_at: string | null) => ({
  ...{ scale_id, scale_title: scale_id.toUpperCase(), enabled: true, period_days },
  ...{ default_period_days: 14, updated_at, updated_by: updated_at && DOCTOR.id },
  updated_by_name: updated_at && DOCTOR.full_name,
});
export const SURVEY_SETTINGS = {
  allowed_period_days: [7, 14],
  items: [survey("gad-7", 14, null), survey("phq-9", 7, "2026-10-12T09:00:00Z")],
};

export const SCHEDULE = {
  today: "2026-10-19",
  items: [
    {
      scale_id: "phq-9",
      period_days: 7,
      grace_days: 3,
      planned: [
        { planned_on: "2026-10-05", status: "done", assessment_id: null },
        { planned_on: "2026-10-12", status: "missed", assessment_id: null },
        { planned_on: "2026-10-19", status: "done", assessment_id: null },
      ],
    },
  ],
};

export const HAMD = {
  scale: {
    id: "hamd-17",
    title: "Шкала Гамильтона для оценки депрессии (HAMD-17)",
    instruction: "",
    version: 1,
    purpose: "severity",
    respondent: "clinician",
    status: "reviewed",
    items: [],
    license: { terms: "t", allowed: true, source: SOURCE_REF },
    scores: [
      {
        id: "total",
        items: [],
        bands: [{ min: 0, max: 52, code: "any", label: "Любая", source: SOURCE_REF }],
      },
    ],
    validation: [SOURCE_REF],
  },
  sources: [],
};

/** Подмена API окна. Неизвестные пути уходят в подмену сессии. */
export async function mockWindow(context: BrowserContext) {
  await context.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const byPatient = /^\/api\/v1\/patients\/([^/]+)(?:\/(.+))?$/.exec(path);
    const id = byPatient?.[1] ?? "";
    const json = (() => {
      if (path === "/api/v1/patients") return PATIENTS.map(([p]) => patient(p));
      if (path === "/api/v1/alerts") return ALERTS;
      if (path === "/api/v1/messaging/unread") return [];
      if (path === "/api/v1/proposals/summary") {
        return {
          pending: PROPOSALS.filter((p) => p.status === "proposed").length,
          decided_today: 0,
        };
      }
      if (path === "/api/v1/alerts/decisions") return DECISIONS;
      if (path === "/api/v1/alerts/thresholds") return [];
      if (path === "/api/v1/assessments/clinician-scales") return { items: [HAMD] };
      const scale = /^\/api\/v1\/assessments\/scales\/(phq-9|gad-7)$/.exec(path)?.[1];
      if (scale === "phq-9" || scale === "gad-7") return definition(scale);
      if (!byPatient) return null;
      switch (byPatient[2]) {
        case undefined:
          return patient(id);
        case "alerts":
          return ALERTS.filter((a) => a.patient_id === id);
        case "proposals":
          return PROPOSALS.filter((p) => p.patient_id === id);
        case "assessments/settings/gad-7":
          return { ...SURVEY_SETTINGS.items[0], period_days: 7 };
        case "assessments/settings":
          return id === LEBEDEVA ? SURVEY_SETTINGS : { allowed_period_days: [7, 14], items: [] };
        case "assessments/schedule":
          return id === LEBEDEVA ? SCHEDULE : { today: "2026-10-19", items: [] };
        case "assessments":
          return { items: RESULTS.filter((r) => r.patient_id === id), next_cursor: null };
        case "diagnoses":
          return id === LEBEDEVA
            ? [
                {
                  id: "d1",
                  icd10: "F41.1",
                  title: "Генерализованное тревожное расстройство",
                  set_at: "2026-09-22T08:00:00Z",
                  set_by: DOCTOR.id,
                  resolved_at: null,
                  resolved_by: null,
                },
              ]
            : [];
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

/** `setup` — свои подмены поверх общих: зарегистрированные позже главнее. */
export async function openWindow(
  page: Page,
  path = "/doctor/",
  setup?: (context: BrowserContext) => Promise<void>,
) {
  await page.clock.setFixedTime(NOW);
  await page.setViewportSize({ width: 1280, height: 832 });
  await mockSessionApi(page.context(), "doctor");
  await mockWindow(page.context());
  await setup?.(page.context());
  await page.goto(path);
}
