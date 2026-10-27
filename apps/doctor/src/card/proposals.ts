/**
 * Предложения системы в карточке (спека #13, разделы 4.1 и 4.3). Тексты сверки с ГРЛС —
 * из спеки #32, раздел 5. Факты истории болезни — спека #30, раздел 6.
 */
import type { Schemas } from "@compass/api-client";
import { DIAGNOSIS, documentIdOf, FACT, factView, isIngestion, MEDICATION } from "./facts";
import { REFERRAL } from "./referral";
import { recordLabel } from "./signals";
import { regimenText } from "./therapy";

type Proposal = Schemas["ProposalOut"];

export const FINDING = "freetext.finding";
export const PRESCRIPTION = "therapy.prescription";
export const STOP = "therapy.stop";

const CATEGORIES: Record<string, string> = {
  symptom: "Симптом",
  side_effect: "Побочный эффект",
  adherence: "Приём препарата",
  medication: "Другой препарат",
};

const ADHERENCE: Record<string, string> = {
  taken: "принимает",
  missed: "пропустил",
  stopped: "бросил",
  dose_changed: "сам изменил дозу",
};

const DRUG_WARNINGS: Record<string, string | null> = {
  registered: null,
  unknown: "статус в ГРЛС не определён",
  suspended: "применение приостановлено",
  inactive: "регистрация не действует",
  not_found: "нет в ГРЛС",
};

type Match = { trade_name: string | null; inn: string | null; reg_number: string };
type Drug = { name: string; check: string; matches: Match[] };

export type ProposalView = {
  title: string;
  summary: string | null;
  quote: string | null;
  recordLabel: string | null;
  adherence: string | null;
  drug: { name: string; warning: string | null } | null;
  fields: { name: string; value: string }[];
};

const CASE_HISTORY: Record<string, string> = {
  [DIAGNOSIS]: "Диагноз из истории болезни",
  [MEDICATION]: "Препарат из истории болезни",
  [FACT]: "Анамнез из истории болезни",
};

/** Документ истории болезни, где факт рассматривается вместе с цитатой и сверкой. */
export function caseHistoryDocument(proposal: Proposal): string | null {
  return documentIdOf(proposal);
}

/** Назначение и отмена решаются в разделе «Терапия», где видны проверки. */
export function isTherapy(proposal: Proposal): boolean {
  return proposal.kind === PRESCRIPTION || proposal.kind === STOP;
}

/** Предложения, которые ждут решения врача, новые первыми. */
export function pendingProposals(proposals: readonly Proposal[]): Proposal[] {
  return newestFirst(proposals.filter((p) => p.status === "proposed"));
}

export function newestFirst(proposals: readonly Proposal[]): Proposal[] {
  return [...proposals].sort((a, b) => b.created_at.localeCompare(a.created_at));
}

/** Короткое название предложения: категория находки или вид предложения. */
export function proposalTitle(proposal: Proposal): string {
  const category = proposal.payload.category;
  if (proposal.kind === FINDING && typeof category === "string") {
    return CATEGORIES[category] ?? category;
  }
  if (proposal.kind === FACT && proposal.payload.field === "risk_factor") {
    return "Фактор риска из истории болезни";
  }
  if (proposal.kind === REFERRAL) {
    return "Направление к психиатру";
  }
  return CASE_HISTORY[proposal.kind] ?? proposal.kind;
}

export function proposalView(proposal: Proposal): ProposalView {
  const title = proposalTitle(proposal);
  if (proposal.kind === PRESCRIPTION || proposal.kind === STOP) {
    return therapyView(proposal);
  }
  if (isIngestion(proposal)) {
    return caseHistoryView(proposal, title);
  }
  if (proposal.kind === REFERRAL) {
    // Решается на экране сигнала (спека #35): здесь — только по какому сигналу.
    const signal = proposal.payload.title;
    return {
      title,
      summary: typeof signal === "string" ? `По сигналу: ${signal}` : null,
      quote: null,
      recordLabel: null,
      adherence: null,
      drug: null,
      fields: [],
    };
  }
  if (proposal.kind !== FINDING) {
    return {
      title,
      summary: null,
      quote: null,
      recordLabel: null,
      adherence: null,
      drug: null,
      fields: Object.entries(proposal.payload).map(([name, value]) => ({
        name,
        value: typeof value === "string" ? value : JSON.stringify(value),
      })),
    };
  }
  const p = proposal.payload;
  const event = typeof p.adherence_event === "string" ? p.adherence_event : null;
  return {
    title,
    summary: typeof p.summary === "string" ? p.summary : null,
    quote: typeof p.quote === "string" ? p.quote : null,
    recordLabel: recordLabel(p),
    adherence: event ? (ADHERENCE[event] ?? event) : null,
    drug: isDrug(p.drug) ? { name: p.drug.name, warning: drugWarning(p.drug) } : null,
    fields: [],
  };
}

/** Назначение и отмена (спека #28): препарат, показание и схема из снимка справочника. */
function therapyView(proposal: Proposal): ProposalView {
  const p = proposal.payload;
  const empty = { quote: null, recordLabel: null, adherence: null, drug: null, fields: [] };
  if (proposal.kind === STOP) {
    return { ...empty, title: "Отмена назначения", summary: String(p.reason ?? "") || null };
  }
  const prefix = p.off_label === true ? "Назначение вне инструкции" : "Назначение";
  const regimen = p.regimen as Schemas["LabelRegimen"];
  return {
    ...empty,
    title: `${prefix}: ${String(p.trade_name)}`,
    summary: `${String(p.indication_title)}: ${regimenText(regimen)}`,
  };
}

/** Факт истории болезни: что найдено и цитата; решается на экране документа. */
function caseHistoryView(proposal: Proposal, title: string): ProposalView {
  const view = factView(proposal.kind, proposal.payload);
  const details = view.subtitleIsQuote ? null : view.subtitle;
  // Название уже в кратком описании: строка препарата нужна только для предупреждения сверки.
  const warning = proposal.kind === MEDICATION && view.marker?.tone === "warning";
  return {
    title,
    summary: [view.title, details].filter(Boolean).join(" — "),
    quote: view.quote || null,
    recordLabel: null,
    adherence: null,
    drug: warning && view.marker ? { name: view.title, warning: view.marker.text } : null,
    fields: [],
  };
}

function drugWarning(drug: Drug): string | null {
  if (drug.check === "similar") {
    const names = drug.matches.map((m) =>
      m.trade_name && m.inn
        ? `${m.trade_name} (${m.inn})`
        : (m.trade_name ?? m.inn ?? m.reg_number),
    );
    return `возможно: ${names.join(", ")}`;
  }
  return drug.check in DRUG_WARNINGS ? (DRUG_WARNINGS[drug.check] ?? null) : drug.check;
}

function isDrug(value: unknown): value is Drug {
  return typeof value === "object" && value !== null && "name" in value && "check" in value;
}
