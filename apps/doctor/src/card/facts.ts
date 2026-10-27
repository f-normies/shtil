/**
 * Факты из истории болезни (спека #30, разделы 2, 4 и 6; кадр 07в): группы, строки фактов,
 * решённые и уже известные факты, ошибки решения. Чистые функции, без React.
 */
import type { Schemas } from "@compass/api-client";
import { dayText, momentText, plural, whenText } from "./documents";
import { detailOf } from "./errors";

type Proposal = Schemas["ProposalOut"];
type DocumentOut = Schemas["DocumentOut"];
type KnownFact = Schemas["KnownFact"];
type Drug = Schemas["DrugCheckOut"];
type Payload = Record<string, unknown>;

export const DIAGNOSIS = "ingestion.diagnosis";
export const MEDICATION = "ingestion.medication";
export const FACT = "ingestion.fact";

export type GroupKey = "diagnoses" | "medications" | "anamnesis" | "risk_factors";
export type FactGroup = { key: GroupKey; title: string; pending: Proposal[] };
export type DocumentFacts = {
  groups: FactGroup[];
  known: KnownFact[];
  decided: Proposal[];
  pending: number;
};
export type Marker = { tone: "ok" | "warning" | "risk"; text: string };
export type FactView = {
  title: string;
  subtitle: string | null;
  /** Подзаголовок — сама цитата: в раскрытой строке он не повторяется. */
  subtitleIsQuote: boolean;
  marker: Marker | null;
  quote: string;
  page: number | null;
  /** «Если подтвердить»: что изменится в карточке. */
  effect: string;
  kindLabel: string;
};
export type RowView = { title: string; subtitle: string; page: number | null };
export type DecidedView = { title: string; subtitle: string; confirmed: boolean; time: string };

const GROUPS: { key: GroupKey; title: string }[] = [
  { key: "diagnoses", title: "Диагнозы" },
  { key: "medications", title: "Препараты" },
  { key: "anamnesis", title: "Анамнез" },
  { key: "risk_factors", title: "Факторы риска" },
];

const DRUG_CHECKS: Record<string, string> = {
  registered: "Есть в ГРЛС",
  not_found: "Нет в ГРЛС: проверьте название",
  inactive: "Регистрация в ГРЛС не действует",
  suspended: "Применение приостановлено в ГРЛС",
  unknown: "Статус в ГРЛС не определён",
};

const ALREADY_DECIDED = "Proposal is already decided";
const CANNOT_APPLY = "Proposal kind cannot be applied";
const GENERIC_ERROR = "решение не сохранено, проверьте связь и попробуйте ещё раз";

export function isIngestion(proposal: Proposal): boolean {
  return proposal.kind.startsWith("ingestion.");
}

/** Документ, из которого пришло предложение `ingestion.*`. */
export function documentIdOf(proposal: Proposal): string | null {
  return isIngestion(proposal) ? (text(proposal.payload, "document_id") ?? null) : null;
}

/** Факты документа: ожидающие по группам, решённые и те, что уже были в карточке. */
export function documentFacts(
  proposals: readonly Proposal[],
  document: DocumentOut,
): DocumentFacts {
  const own = proposals.filter((p) => documentIdOf(p) === document.id);
  const pending = own.filter((p) => p.status === "proposed").sort(byPage);
  const groups = GROUPS.map(({ key, title }) => ({
    key,
    title,
    pending: pending.filter((p) => groupOf(p) === key),
  })).filter((g) => g.pending.length > 0);
  const decided = own
    .filter((p) => p.status !== "proposed")
    .sort((a, b) => (a.decided_at ?? "").localeCompare(b.decided_at ?? ""));
  return {
    groups,
    known: document.known,
    decided,
    pending: groups.reduce((n, g) => n + g.pending.length, 0),
  };
}

export function factView(kind: string, payload: Payload): FactView {
  const common = {
    quote: text(payload, "quote") ?? "",
    page: typeof payload.page === "number" ? payload.page : null,
  };
  if (kind === DIAGNOSIS) {
    const code = text(payload, "icd10") ?? "";
    return {
      ...common,
      title: text(payload, "text") ?? code,
      subtitle: payload.code_in_text === true ? code : `${code} · код предложен моделью`,
      subtitleIsQuote: false,
      marker: null,
      effect: `Диагноз ${code} появится в карточке`,
      kindLabel: "диагноз",
    };
  }
  if (kind === MEDICATION) {
    return { ...common, ...medicationView(payload) };
  }
  const risk = payload.field === "risk_factor";
  return {
    ...common,
    title: text(payload, "fact") ?? "",
    subtitle: `«${common.quote}»`,
    subtitleIsQuote: true,
    marker: risk ? { tone: "risk", text: "Фактор риска" } : null,
    effect: risk
      ? "Фактор риска сохранится в карточке пациента"
      : "Факт сохранится в анамнезе пациента",
    kindLabel: risk ? "фактор риска" : "анамнез",
  };
}

function medicationView(payload: Payload): Omit<FactView, "quote" | "page"> {
  const stoppedOn = text(payload, "stopped_on");
  const documentDate = text(payload, "document_date");
  const past = payload.status === "past";
  // Дата отмены, вычисленная от даты документа («отменили 10 дней назад»), в тексте не написана.
  const computed =
    payload.stopped_on_source === "computed" ? ", дата вычислена от даты документа" : "";
  const status = past
    ? stoppedOn
      ? `отменён ${dayText(stoppedOn)}${computed}`
      : "отменён, дата не указана"
    : documentDate
      ? `принимал на ${dayText(documentDate)}`
      : "принимал на дату документа";
  const dose = [text(payload, "dose"), text(payload, "regimen")].filter(Boolean).join(", ");
  const renamed =
    payload.renamed === true ? `в тексте: «${text(payload, "name_in_text") ?? ""}»` : null;
  let effect = "Препарат появится в карточке как текущий, проверки терапии учтут его";
  if (past) {
    effect = stoppedOn
      ? `Препарат появится в карточке как отменённый ${dayText(stoppedOn)}, проверки терапии учтут отмену`
      : "Сохранится как прошлый приём без даты отмены. В список препаратов не попадёт";
  }
  return {
    title: text(payload, "name") ?? "",
    subtitle: [dose, status, renamed].filter(Boolean).join(" · "),
    subtitleIsQuote: false,
    marker: isDrug(payload.drug) ? drugCheck(payload.drug) : null,
    effect,
    kindLabel: "препарат",
  };
}

/** Сверка названия с ГРЛС словами; всё, кроме `registered`, выделяется (кадр 04). */
export function drugCheck(drug: Drug): Marker {
  if (drug.check === "similar") {
    const names = drug.matches.map((m) => (m.trade_name ? `${m.trade_name} (${m.inn})` : m.inn));
    return { tone: "warning", text: `Возможно, в ГРЛС: ${names.join(", ")}` };
  }
  const known = DRUG_CHECKS[drug.check];
  return {
    tone: drug.check === "registered" ? "ok" : "warning",
    text: known ?? `Сверка с ГРЛС: ${drug.check}`,
  };
}

export function knownView(fact: KnownFact): RowView {
  const view = factView(fact.kind, fact.payload);
  return fact.kind === DIAGNOSIS
    ? {
        title: `${text(fact.payload, "icd10") ?? ""} ${view.title}`,
        subtitle: "Диагноз уже есть в карточке, предложения нет",
        page: view.page,
      }
    : {
        title: view.title,
        subtitle: "Препарат уже есть в карточке, предложения нет",
        page: view.page,
      };
}

export function decidedView(
  proposal: Proposal,
  me: { id: string; full_name: string },
  now: Date,
): DecidedView {
  const view = factView(proposal.kind, proposal.payload);
  const mine = proposal.decided_by === me.id;
  const confirmed = proposal.status === "confirmed";
  const comment = proposal.decision_comment?.trim();
  let subtitle: string;
  if (confirmed) {
    subtitle = `${mine ? `Подтверждено врачом, ${shortName(me.full_name)}` : "Подтверждено другим врачом"} · ${view.kindLabel}`;
  } else if (comment) {
    subtitle = `Отклонено: ${comment} · ${mine ? shortName(me.full_name) : "другой врач"}`;
  } else {
    subtitle = `${mine ? `Отклонено врачом, ${shortName(me.full_name)}` : "Отклонено другим врачом"} · ${view.kindLabel}`;
  }
  return {
    title: view.title,
    subtitle,
    confirmed,
    time: proposal.decided_at ? momentText(proposal.decided_at, now) : "",
  };
}

export function waitingLabel(n: number): string {
  return `${plural(n, ["ждёт", "ждут", "ждут"])} решения: ${n}`;
}

/** Строка под заголовком «Данные из документа»: когда разобран и сколько ждёт решения. */
export function factsSummary(
  document: DocumentOut,
  counts: { pending: number; decided: number; known: number },
  now: Date,
): string {
  const parsed = document.extracted_at ? `Разобран ${whenText(document.extracted_at, now)}` : null;
  const n = counts.pending;
  let state = "фактов не найдено";
  if (n > 0) {
    state = `${n} ${plural(n, ["факт", "факта", "фактов"])} ${plural(n, ["ждёт", "ждут", "ждут"])} решения`;
  } else if (counts.decided + counts.known > 0) {
    state = "все факты рассмотрены";
  }
  return [parsed, state].filter(Boolean).join(" · ");
}

/**
 * Ошибка подтверждения или отклонения по-русски. `stale` — решение уже принято в другой
 * вкладке: список нужно обновить. Отказ applier'а (422) приходит по-русски и показывается.
 */
export function decisionErrorText(error: unknown): { text: string; stale: boolean } {
  const detail = detailOf(error);
  if (detail === ALREADY_DECIDED) {
    return { text: "предложение уже рассмотрено", stale: true };
  }
  if (detail === CANNOT_APPLY) {
    return {
      text: "система не умеет применять предложения этого вида, решение не сохранено",
      stale: false,
    };
  }
  const status = (error as { status?: unknown } | null)?.status;
  if (status === 422 && typeof detail === "string" && detail) {
    return { text: lowerFirst(detail), stale: false };
  }
  return { text: GENERIC_ERROR, stale: false };
}

/** «Соколова Ирина Павловна» → «Соколова И. П.». */
export function shortName(fullName: string): string {
  const [surname, ...rest] = fullName.trim().split(/\s+/);
  return [surname, ...rest.map((part) => `${part.charAt(0)}.`)].join(" ");
}

/** После решения раскрывается следующий факт группы (кадр 07в, «Взаимодействия»). */
export function nextPending(group: readonly Proposal[], decidedId: string): string | null {
  const index = group.findIndex((p) => p.id === decidedId);
  return index < 0 ? null : (group[index + 1]?.id ?? null);
}

function groupOf(proposal: Proposal): GroupKey | null {
  if (proposal.kind === DIAGNOSIS) {
    return "diagnoses";
  }
  if (proposal.kind === MEDICATION) {
    return "medications";
  }
  if (proposal.kind === FACT) {
    return proposal.payload.field === "risk_factor" ? "risk_factors" : "anamnesis";
  }
  return null;
}

/** Порядок документа: по странице цитаты, внутри страницы — по порядку создания. */
function byPage(a: Proposal, b: Proposal): number {
  const page = (p: Proposal) =>
    typeof p.payload.page === "number" ? p.payload.page : Number.POSITIVE_INFINITY;
  return page(a) - page(b) || a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id);
}

function text(payload: Payload, key: string): string | undefined {
  const value = payload[key];
  return typeof value === "string" && value ? value : undefined;
}

function isDrug(value: unknown): value is Drug {
  return (
    typeof value === "object" &&
    value !== null &&
    "check" in value &&
    "matches" in value &&
    Array.isArray(value.matches)
  );
}

function lowerFirst(value: string): string {
  // «МКБ» и другие сокращения не трогаем: вторая буква тоже прописная.
  return value.charAt(1) === value.charAt(1).toUpperCase()
    ? value
    : value.charAt(0).toLowerCase() + value.slice(1);
}
