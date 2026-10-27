import type { Schemas } from "@compass/api-client";
import type { BrowserContext } from "@playwright/test";
import { user } from "./session-api-mock";

/**
 * Подменённый API экрана «Предложения» (спека #96): очередь врача, сводка за день,
 * решения и список пациента в карточке. Пациенты, записи и препараты выдуманы.
 */

export type Item = Schemas["QueueItemOut"];
type Status = Item["status"];

const DOCTOR = user("doctor");
export const ERSHOV = "00000000-0000-4000-8000-0000000000e1";
export const LEBEDEVA = "00000000-0000-4000-8000-0000000000e2";
const NAMES: Record<string, string> = {
  [ERSHOV]: "Ершов Павел Ильич",
  [LEBEDEVA]: "Лебедева Марина Викторовна",
};
const RUN = {
  model: "qwen3.6",
  prompt_version: "extract-7a3603ce",
  created_at: "2026-10-19T05:31:00Z",
};
const LABEL = {
  source: "synthetin-label",
  locator: "4.2",
  title: "СИНТЕТИН: общая характеристика",
  url: "https://example.invalid/register",
};

function item(id: string, patientId: string, overrides: Partial<Item>): Item {
  return {
    id,
    patient_id: patientId,
    patient_name: NAMES[patientId] ?? "",
    kind: "freetext.finding",
    payload: {},
    origin_type: "ai_run",
    origin_ref: "run",
    status: "proposed",
    created_at: "2026-10-19T05:30:00Z",
    decided_by: null,
    decided_at: null,
    decision_comment: null,
    run: RUN,
    ...overrides,
  };
}

function finding(payload: Record<string, unknown>) {
  return {
    analysis_id: "an1",
    source_type: "diary",
    source_id: "rec1",
    transcript: true,
    recorded_at: "2026-10-19T05:30:00Z",
    adherence_event: null,
    drug: null,
    ...payload,
  };
}

export function initial(): Item[] {
  return [
    item("jaw", ERSHOV, {
      payload: finding({
        category: "side_effect",
        summary: "сведение челюсти, потливость",
        quote: "вчера вечером пил суматриптан от мигрени, с утра сводит челюсть, весь день мокрый",
      }),
    }),
    item("triptan", ERSHOV, {
      payload: finding({
        category: "medication",
        summary: "суматриптан от мигрени",
        quote: "пил суматриптан от мигрени",
        drug: { name: "Суматриптан", check: "registered", matches: [] },
      }),
    }),
    item("sleep", LEBEDEVA, {
      created_at: "2026-10-18T18:32:00Z",
      payload: finding({
        analysis_id: "an2",
        transcript: false,
        recorded_at: undefined,
        category: "symptom",
        summary: "ночные пробуждения",
        quote: "Ночью опять просыпалась, потом долго не могла уснуть.",
      }),
    }),
    item("synthetin", LEBEDEVA, {
      kind: "therapy.prescription",
      origin_type: "rule",
      run: null,
      created_at: "2026-10-18T09:00:00Z",
      payload: {
        trade_name: "Синтетин",
        indication_title: "Синтетическое расстройство",
        off_label: false,
        regimen: {
          form: "таблетки 20 мг",
          dose_mg: 20,
          times_per_day: 2,
          schedule: "утром и вечером",
          duration_days: null,
          max_days: null,
          duration_text: "определяет врач",
          source: LABEL,
        },
        checks: [
          {
            id: "indication",
            title: "Показание",
            status: "pass",
            detail: "диагноз F41.1",
            source: { ...LABEL, locator: "4.1" },
          },
          {
            id: "age",
            title: "Возраст от 18 лет",
            status: "pass",
            detail: "38 лет",
            source: LABEL,
          },
        ],
      },
    }),
    item("taken", LEBEDEVA, {
      status: "confirmed",
      decided_by: DOCTOR.id,
      decided_at: "2026-10-15T07:10:00Z",
      payload: finding({
        category: "adherence",
        summary: "принимает до завтрака",
        adherence_event: "taken",
      }),
    }),
    item("missed", LEBEDEVA, {
      status: "rejected",
      decided_by: DOCTOR.id,
      decided_at: "2026-10-08T07:10:00Z",
      decision_comment: "приняла позже, уточнила в чате",
      payload: finding({
        category: "adherence",
        summary: "пропуск утренней дозы",
        adherence_event: "missed",
      }),
    }),
  ];
}

/**
 * Два предложения одного пациента одинаковой высоты: кнопка «Подтвердить предложение»
 * у второго стоит там же, где у первого.
 */
export function twins(): Item[] {
  const twin = (id: string, category: string) =>
    item(id, ERSHOV, {
      payload: finding({ category, summary: "плохой сон", quote: "плохо сплю третью ночь" }),
    });
  return [twin("first", "symptom"), twin("second", "side_effect")];
}

export type Reply = { status: number; detail: string };

/**
 * `replies` — ответ с ошибкой на решение по id; `pageSize` — страницы очереди с курсором
 * (номер первого предложения следующей страницы); `decisionDelayMs` — задержка ответа
 * на решение.
 */
export type QueueOptions = {
  replies?: Record<string, Reply>;
  pageSize?: number;
  decisionDelayMs?: number;
};

/** Очередь на подменённом API: решения меняют статус предложения. */
export async function mockQueue(
  context: BrowserContext,
  items: Item[],
  { replies = {}, pageSize = Number.POSITIVE_INFINITY, decisionDelayMs = 0 }: QueueOptions = {},
) {
  const posted: { path: string; body: unknown }[] = [];
  const queries: string[] = [];
  let decidedToday = 3;
  await context.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const decision = /^\/api\/v1\/proposals\/([^/]+)\/(confirm|reject)$/.exec(path);
    if (request.method() === "POST" && decision) {
      const [, id = "", verb] = decision;
      posted.push({ path, body: request.postDataJSON() });
      await new Promise((resolve) => setTimeout(resolve, decisionDelayMs));
      const reply = replies[id];
      if (reply) {
        const index = items.findIndex((i) => i.id === id);
        if (reply.status === 404 && index >= 0) {
          // Пациента передали другому врачу: предложение пропало из очереди.
          items.splice(index, 1);
        } else if (reply.detail === "Proposal is already decided" && items[index]) {
          // Решили в другой вкладке: в списке предложение уже подтверждено.
          items[index].status = "confirmed";
        }
        await route.fulfill({
          status: reply.status,
          json: { status: reply.status, title: "Ошибка", detail: reply.detail },
        });
        return;
      }
      const target = items.find((i) => i.id === id);
      if (target) {
        target.status = verb === "confirm" ? "confirmed" : "rejected";
        target.decided_by = DOCTOR.id;
        target.decided_at = new Date().toISOString();
        decidedToday += 1;
      }
      await route.fulfill({ json: target });
      return;
    }
    if (path.startsWith("/api/v1/knowledge/sources/")) {
      await route.fulfill({
        json: {
          source: {
            id: LABEL.source,
            title: LABEL.title,
            kind: "drug_label",
            publisher: "Синтетический издатель",
            version: "0002",
            date: "2026-10-14",
            url: LABEL.url,
            supplementary: false,
          },
          registry: { reg_number: "ЛП-000000", status: "active", snapshot_date: "2026-10-16" },
          locator: url.searchParams.get("locator"),
          found: true,
          sections: [],
        },
      });
      return;
    }
    const json = (() => {
      if (path === "/api/v1/proposals") {
        queries.push(url.search);
        const status = (url.searchParams.get("status") ?? "proposed") as Status;
        const q = (url.searchParams.get("q") ?? "").toLowerCase();
        const shown = items.filter(
          (i) => i.status === status && i.patient_name.toLowerCase().includes(q),
        );
        const start = Number(url.searchParams.get("cursor") ?? 0);
        const end = start + pageSize;
        return {
          items: shown.slice(start, end),
          next_cursor: end < shown.length ? String(end) : null,
        };
      }
      if (path === "/api/v1/proposals/summary") {
        return {
          pending: items.filter((i) => i.status === "proposed").length,
          decided_today: decidedToday,
        };
      }
      const own = /^\/api\/v1\/patients\/([^/]+)\/proposals$/.exec(path)?.[1];
      if (own) {
        return items.filter((i) => i.patient_id === own).map(({ patient_name, run, ...p }) => p);
      }
      const patient = /^\/api\/v1\/patients\/([^/]+)$/.exec(path)?.[1];
      if (patient) {
        return {
          id: patient,
          full_name: NAMES[patient],
          email: "p@example.invalid",
          lead_doctor_id: DOCTOR.id,
          created_at: "2026-09-22T09:00:00Z",
          phone: null,
          birth_date: "1988-05-01",
          sex: patient === ERSHOV ? "male" : "female",
        };
      }
      if (path === "/api/v1/alerts" || /\/(alerts|diagnoses|prescriptions)$/.test(path)) return [];
      return null;
    })();
    if (json === null) {
      await route.fallback();
      return;
    }
    await route.fulfill({ json });
  });
  return { posted, queries };
}
