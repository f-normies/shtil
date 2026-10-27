import type { Schemas } from "@compass/api-client";
import type { BrowserContext } from "@playwright/test";
import { check, DOCTOR, ORLOVA, PROPOSAL } from "./doctor-therapy-mock";

/**
 * «Терапия» для проверки #36: предупреждения о безопасности (#29) во всех видах.
 * Препараты, реакции и пациентка выдуманы.
 */

type Payload = { checks: ReturnType<typeof check>[] } & Record<string, unknown>;
const payload = PROPOSAL.payload as Payload;

/** Предложение с проверками безопасности: «уточните» и «желательно». */
const CAUTIOUS: Schemas["ProposalOut"] = {
  ...PROPOSAL,
  payload: {
    ...payload,
    checks: [
      ...payload.checks,
      check("contra:lactose", "Непереносимость лактозы", "manual", "В карте нет сведений"),
      check("caution:alcohol", "Приём алкоголя", "warn", "Предупредите пациентку"),
    ],
  },
};

/** Подбор, где противопоказание не дало создать предложение: «Назначить нельзя». */
const BLOCKED = {
  message: null,
  options: [
    {
      ...payload,
      checks: [
        ...payload.checks,
        check("contra:maoi", "Приём ингибиторов МАО", "fail", "Моклобемид в списке препаратов"),
      ],
      proposal: null,
    },
  ],
};

const PRESCRIPTION: Schemas["PrescriptionOut"] = {
  id: "rx-1",
  patient_id: ORLOVA,
  proposal_id: "proposal-0",
  drug_id: "aviandr",
  trade_name: "Авиандр",
  inn: "маритупирдин",
  indication_id: "post-covid-anxiety",
  dose_mg: 20,
  times_per_day: 3,
  schedule: "утром, днём и вечером",
  duration_days: 28,
  started_at: "2026-10-01T08:00:00Z",
  ends_on: "2026-10-29",
  off_label: true,
  off_label_reason: "Доза выше инструкции по решению врача",
  status: "active",
  stop_reason: null,
  stopped_at: null,
  stopped_by: null,
  confirmed_by: DOCTOR.id,
  card_snapshot: {},
};

const REACTIONS: Schemas["ReactionReportOut"][] = [
  {
    id: "r-1",
    prescription_id: "rx-1",
    reported_at: "2026-10-18T19:00:00Z",
    note: "Сильная сыпь с утра",
    reactions: [{ id: "rash", level: "mandatory", title: "Кожная сыпь", patient_title: "сыпь" }],
  },
  {
    id: "r-2",
    prescription_id: "rx-1",
    reported_at: "2026-10-12T19:00:00Z",
    note: null,
    reactions: [
      {
        id: "dizziness",
        level: "desirable",
        title: "Головокружение",
        patient_title: "кружится голова",
      },
    ],
  },
];

const MEDICATIONS: Schemas["MedicationOut"][] = [
  {
    id: "m-1",
    name: "Успокоительный сбор",
    inn: null,
    registry_check: "not_found",
    started_on: "2026-09-01",
    stopped_on: null,
    entered_by: DOCTOR.id,
    created_at: "2026-09-29T08:00:00Z",
  },
];

/** Подменить ответы «Терапии» поверх `openTherapy`: `blocked` — подбор с противопоказанием. */
export function therapyRisks({ blocked = false } = {}) {
  return async (context: BrowserContext) => {
    const base = `/api/v1/patients/${ORLOVA}`;
    await context.route(`**${base}/proposals`, (route) =>
      route.fulfill({ json: blocked ? [] : [CAUTIOUS] }),
    );
    await context.route(`**${base}/therapy/options`, (route) => route.fulfill({ json: BLOCKED }));
    await context.route(`**${base}/prescriptions`, (route) =>
      route.fulfill({ json: [PRESCRIPTION] }),
    );
    await context.route(`**${base}/reactions`, (route) => route.fulfill({ json: REACTIONS }));
    await context.route(`**${base}/medications`, (route) => route.fulfill({ json: MEDICATIONS }));
  };
}
