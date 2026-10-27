import type { Schemas } from "@compass/api-client";
import type { BrowserContext, Page } from "@playwright/test";
import { mockSessionApi, user } from "./session-api-mock";

/**
 * «Терапия» по кадру «Врач v4 · 10» (спека #28, раздел 8) на подменённом API. Пациентка
 * и данные выдуманы; препарат и показание — как в сценарии 2.
 */

export const DOCTOR = user("doctor");
export const ORLOVA = "00000000-0000-4000-8000-0000000000d4";
export const NOW = new Date("2026-10-19T12:00:00+03:00");
export const OHLP = {
  source: "aviandr-ohlp",
  locator: "4.1",
  title: "ОХЛП «Авиандр»",
  url: "https://example.invalid/ohlp",
};

export function check(id: string, title: string, status: string, detail: string) {
  return { id, title, status, detail, source: OHLP, actions: [] };
}

export const PROPOSAL: Schemas["ProposalOut"] = {
  id: "proposal-1",
  patient_id: ORLOVA,
  kind: "therapy.prescription",
  origin_type: "rule",
  origin_ref: "aviandr/post-covid-anxiety",
  status: "proposed",
  created_at: "2026-10-19T08:00:00Z",
  decided_by: null,
  decided_at: null,
  decision_comment: null,
  payload: {
    drug_id: "aviandr",
    trade_name: "Авиандр",
    inn: "маритупирдин",
    indication_id: "post-covid-anxiety",
    indication_title: "тревожное состояние после COVID-19",
    indication_icd10: ["F43.2", "F43.8"],
    regimen: {
      form: "таблетки, покрытые плёночной оболочкой, 20 мг",
      strength_mg: 20,
      dose_mg: 20,
      times_per_day: 2,
      schedule: "утром и вечером",
      duration_days: 28,
      max_days: 42,
      duration_text: "4 недели, при необходимости до 6",
      source: { ...OHLP, locator: "4.2" },
    },
    instructions: [{ text: "Внутрь за 30 минут до еды", source: { ...OHLP, locator: "4.2" } }],
    checks: [
      check("registered", "Зарегистрирован в РФ", "pass", "ЛП-№(002445)-(РГ-RU), действует"),
      check("not_prescribed", "Сейчас не назначен", "pass", "Других назначений «Авиандра» нет"),
      check("indication", "Показание: тревога после COVID-19", "pass", "F43.2 + U09.9 у пациентки"),
      check("age", "Возраст от 18 лет", "pass", "Пациентке 38 лет"),
      check("severity", "Лёгкая или средняя степень выраженности", "manual", "Проверьте сами"),
    ],
  },
};

/** Ответ подбора — то же предложение, что в списке предложений. */
export function optionsOut() {
  const p = PROPOSAL.payload;
  return { message: null, options: [{ ...p, proposal: PROPOSAL }] };
}

export async function mockTherapy(context: BrowserContext, posted: string[]) {
  // После решения предложение уже не ждёт: список отдаёт его рассмотренным.
  let decided = false;
  await context.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === "POST") {
      posted.push(path);
      if (path.endsWith("/therapy/options")) {
        await route.fulfill({ json: optionsOut() });
        return;
      }
      decided = true;
      await route.fulfill({ json: { id: "rx-1" } });
      return;
    }
    const json = (() => {
      if (path === "/api/v1/patients") return [patient()];
      if (path === `/api/v1/patients/${ORLOVA}`) return patient();
      if (path === "/api/v1/alerts" || path === "/api/v1/messaging/unread") return [];
      if (path === `/api/v1/patients/${ORLOVA}/proposals`) {
        return [decided ? { ...PROPOSAL, status: "confirmed" } : PROPOSAL];
      }
      if (path === `/api/v1/patients/${ORLOVA}/therapy/guidance`) {
        return {
          status: "done",
          stale: false,
          claims: [],
          computed_at: null,
          guidelines: [],
          catalog_drugs: [],
        };
      }
      if (path === `/api/v1/patients/${ORLOVA}/assessments`) {
        return { items: [], next_cursor: null };
      }
      if (path.startsWith(`/api/v1/patients/${ORLOVA}/`)) return [];
      return null;
    })();
    if (json === null) {
      await route.fallback();
      return;
    }
    await route.fulfill({ json });
  });
}

export function patient(): Schemas["PatientSummary"] {
  return {
    access: "lead",
    id: ORLOVA,
    full_name: "Орлова Наталья Петровна",
    email: "p@example.invalid",
    phone: null,
    lead_doctor_id: DOCTOR.id,
    created_at: "2026-09-29T08:00:00Z",
    birth_date: "1988-03-01",
    sex: "female",
    pregnant: null,
    breastfeeding: null,
    child_pugh: null,
    egfr: null,
    egfr_measured_on: null,
  };
}

/** `setup` — свои подмены поверх общих: зарегистрированные позже главнее. */
export async function openTherapy(page: Page, setup?: (context: BrowserContext) => Promise<void>) {
  const posted: string[] = [];
  await page.clock.setFixedTime(NOW);
  await page.setViewportSize({ width: 1280, height: 832 });
  await mockSessionApi(page.context(), "doctor");
  await mockTherapy(page.context(), posted);
  await setup?.(page.context());
  await page.goto(`/doctor/patients/${ORLOVA}/therapy`);
  return posted;
}
