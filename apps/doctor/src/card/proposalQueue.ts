/**
 * Очередь предложений врача (спека #96, раздел 3.5; кадры «Врач v4 · 04» и «07б»): группы
 * по пациентам, прогресс за день, выбор после решения, связанные предложения, строка списка,
 * источник находки, применимость и ошибки решения. Чистые функции, без React. Заголовки
 * и описания по видам — `proposals.ts`.
 */
import type { Schemas } from "@compass/api-client";
import { dayText, OCR_NOTE } from "./documents";
import { detailOf } from "./errors";
import { decisionErrorText, documentIdOf, isIngestion } from "./facts";
import { FINDING, isTherapy, PRESCRIPTION, proposalView } from "./proposals";
import { REFERRAL } from "./referral";
import { type Basis, refKey, type SourceRef } from "./sources";

type Proposal = Schemas["ProposalOut"];
type Check = Pick<Schemas["CheckOut"], "id" | "title" | "status" | "detail" | "source">;
type Payload = Record<string, unknown>;

/**
 * Предложение в очереди. У `GET /proposals` (`QueueItemOut`) есть имя пациента и сведения
 * о разборе из `ai_runs` (спека #96, 3.2); у списка пациента в карточке их нет.
 */
export type QueueItem = Proposal & { patient_name?: string; run?: Schemas["RunInfo"] | null };
export type QueueStatus = Proposal["status"];
export type PatientGroup = { patientId: string; name: string; items: QueueItem[] };
export type Progress = { decided: number; total: number; text: string; ratio: number };
export type ApplicabilityIcon = "drug" | "calendar" | "indication" | "profile" | "document";
/** Строка «Применимость»: факт из `payload`; `warning` — выделить значком и словом. */
export type ApplicabilityRow = {
  icon: ApplicabilityIcon;
  text: string;
  detail: string | null;
  warning: boolean;
};
export type FindingSource = {
  record: string;
  note: string;
  voice: boolean;
  /** Время записи; у старых находок без `recorded_at` — время предложения. */
  at: string;
  /** «extract-7a3603ce, 19.10.2026»; без строки `ai_runs` — `null`. */
  rules: string | null;
};
export type RowView = { title: string; detail: string | null; meta: string | null };
/** Где решается предложение, если не в очереди. */
export type Place = "therapy" | "signal" | "documents";

/** Вкладки очереди: статус в адресе, «Ждут решения» — без параметра. */
export const STATUS_TABS: readonly { value: QueueStatus; label: string; empty: string }[] = [
  { value: "proposed", label: "Ждут решения", empty: "Все предложения разобраны" },
  { value: "confirmed", label: "Подтверждены", empty: "Подтверждённых предложений нет" },
  { value: "rejected", label: "Отклонены", empty: "Отклонённых предложений нет" },
];

/** Статус из адреса: неизвестное значение — «Ждут решения». */
export function statusOf(value: unknown): QueueStatus {
  return value === "confirmed" || value === "rejected" ? value : "proposed";
}

/** Группы по пациентам в порядке списка (новые выше), внутри группы порядок тот же. */
export function groupByPatient(items: readonly QueueItem[]): PatientGroup[] {
  const groups = new Map<string, PatientGroup>();
  for (const item of items) {
    const group = groups.get(item.patient_id) ?? {
      patientId: item.patient_id,
      name: item.patient_name || "Пациент",
      items: [],
    };
    group.items.push(item);
    groups.set(item.patient_id, group);
  }
  return [...groups.values()];
}

/** «Предложение 1 из 2»: номер внутри пациента. */
export function positionOf(
  groups: readonly PatientGroup[],
  id: string,
): { index: number; total: number } | null {
  for (const group of groups) {
    const index = group.items.findIndex((i) => i.id === id);
    if (index >= 0) {
      return { index: index + 1, total: group.items.length };
    }
  }
  return null;
}

/** «Разобрано сегодня N из M»: N — решено с начала дня, M — N плюс ждущие (спека, 3.4). */
export function progress(summary: { pending: number; decided_today: number }): Progress {
  const decided = summary.decided_today;
  const total = decided + summary.pending;
  return { decided, total, text: `${decided} из ${total}`, ratio: total > 0 ? decided / total : 0 };
}

/** Начало сегодняшнего дня по часовому поясу браузера — `since` для `/proposals/summary`. */
export function startOfToday(now: Date): string {
  return new Date(now.getFullYear(), now.getMonth(), now.getDate()).toISOString();
}

/**
 * Что выбрать после решения: следующее предложение того же пациента, затем оставшиеся
 * выше у него же, затем первое у следующих пациентов, по кругу. Ничего — `null`.
 */
export function nextAfterDecision(groups: readonly PatientGroup[], decidedId: string) {
  const at = groups.findIndex((g) => g.items.some((i) => i.id === decidedId));
  const group = groups[at];
  if (!group) {
    return null;
  }
  const index = group.items.findIndex((i) => i.id === decidedId);
  const same = [...group.items.slice(index + 1), ...group.items.slice(0, index)];
  const others = [...groups.slice(at + 1), ...groups.slice(0, at)];
  return same[0]?.id ?? others.find((g) => g.items.length > 0)?.items[0]?.id ?? null;
}

/** Связанные: находки одного разбора записи, факты одного документа истории болезни. */
export function relatedProposals(item: QueueItem, items: readonly QueueItem[]): QueueItem[] {
  const key = relationKey(item);
  return key ? items.filter((other) => other.id !== item.id && relationKey(other) === key) : [];
}

/** Строка списка: заголовок по виду, краткое описание и откуда предложение. */
export function rowView(item: QueueItem): RowView {
  const view = proposalView(item);
  const summary = view.summary ? upperFirst(view.summary) : null;
  if (item.kind === FINDING) {
    const drug = view.drug ? (view.drug.warning ?? REGISTERED) : null;
    return {
      title: view.title,
      detail: [summary, drug].filter(Boolean).join(" · ") || null,
      meta: findingSource(item).record,
    };
  }
  return {
    title: view.title,
    detail: summary,
    meta: isIngestion(item) ? casePage(item.payload) : null,
  };
}

/** Тип и время записи и версия правил разбора (спека, 3.2 и 3.3). */
export function findingSource(item: QueueItem): FindingSource {
  const p = item.payload;
  const voice = p.transcript === true;
  const chat = p.source_type === "message";
  let record = chat ? "Сообщение в чате" : "Запись в дневнике";
  if (voice) {
    record = chat ? "Голосовое сообщение в чате" : "Голосовая запись в дневнике";
  }
  const recorded = typeof p.recorded_at === "string" && p.recorded_at ? p.recorded_at : null;
  return {
    record,
    // Кадр 07б: в расшифровке названия могут быть распознаны с ошибками.
    note: voice
      ? "Цитата дословно из расшифровки. Названия в ней могут быть распознаны с ошибками"
      : "Цитата дословно из записи",
    voice,
    at: recorded ?? item.created_at,
    rules: rulesText(item),
  };
}

/** «Версия правил разбора»: версия промпта и дата из `ai_runs`; без строки разбора — `null`. */
export function rulesText(item: QueueItem): string | null {
  const run = item.run;
  return run ? `${run.prompt_version ?? "версия не записана"}, ${dateText(run.created_at)}` : null;
}

/**
 * Применимость — только из `payload` (спека, 3.5). Находка: препарат со сверкой с ГРЛС
 * словами и событие приёма. Назначение: показание и профиль из проверок. История болезни:
 * документ и страница.
 */
export function applicability(item: QueueItem): ApplicabilityRow[] {
  const p = item.payload;
  if (item.kind === FINDING) {
    const view = proposalView(item);
    const rows: ApplicabilityRow[] = [];
    if (view.drug) {
      rows.push({
        icon: "drug",
        text: view.drug.name,
        detail: upperFirst(view.drug.warning ?? REGISTERED),
        warning: view.drug.warning !== null,
      });
    }
    if (view.adherence) {
      rows.push({
        icon: "calendar",
        text: `Приём препарата: ${view.adherence}`,
        detail: null,
        warning: false,
      });
    }
    return rows;
  }
  if (item.kind === PRESCRIPTION) {
    return prescriptionRows(p);
  }
  if (isIngestion(item)) {
    const date = typeof p.document_date === "string" && p.document_date ? p.document_date : null;
    const notes = [p.ocr === true ? OCR_NOTE : null, date ? `Документ от ${dayText(date)}` : null];
    return [
      {
        icon: "document",
        text: casePage(p),
        detail: notes.filter(Boolean).join(". ") || null,
        warning: p.ocr === true,
      },
    ];
  }
  return [];
}

/**
 * Основание назначения для инспектора #106: разделы инструкции с показанием и со схемой.
 * Отмена и прочие виды основания по правилу не имеют — `null`.
 */
export function therapyBasis(item: QueueItem): Basis | null {
  if (item.kind !== PRESCRIPTION) {
    return null;
  }
  const p = item.payload;
  const checks = checksOf(p);
  const regimen = p.regimen as { source?: unknown } | undefined;
  const links = [checks.find((c) => c.id === "indication")?.source, regimen?.source];
  const refs = [...new Map(links.filter(isLink).map((l) => [refKey(toRef(l)), toRef(l)])).values()];
  if (refs.length === 0) {
    return null;
  }
  return {
    heading: `Назначение «${String(p.trade_name)}» по показанию «${String(p.indication_title)}»`,
    applicability: checks
      .filter((c) => PROFILE_CHECKS.includes(c.id))
      .map((c) => `${c.title} — ${c.detail}`),
    refs,
  };
}

/** Подпись цитаты (кадр 04, «Доступность»): чьи это слова и откуда. */
export function quoteCaption(item: QueueItem): string {
  if (item.kind === FINDING) {
    return `Цитата пациента, ${lowerFirst(findingSource(item).record)}`;
  }
  const page = item.payload.page;
  return typeof page === "number"
    ? `Цитата из истории болезни, стр. ${page}`
    : "Цитата из истории болезни";
}

/** Решение по предложению: кто и когда; у отклонённого — причина или что её нет. */
export function outcomeText(
  item: QueueItem,
  doctorId: string,
  format: (iso: string) => string,
): { text: string; reason: string | null } {
  const who = item.decided_by === doctorId ? "вами" : "другим врачом";
  const verb = item.status === "confirmed" ? "Подтверждено" : "Отклонено";
  const when = item.decided_at ? `, ${format(item.decided_at)}` : "";
  const comment = item.decision_comment?.trim();
  let reason: string | null = null;
  if (item.status === "rejected") {
    reason = comment ? `Причина: ${comment}` : "Причина не указана";
  }
  return { text: `${verb} ${who}${when}`, reason };
}

/** Где решить, если не здесь: назначение — в «Терапии», направление — у сигнала, факт — у документа. */
export function decidedElsewhere(item: QueueItem): Place | null {
  if (isTherapy(item)) {
    return "therapy";
  }
  if (item.kind === REFERRAL) {
    return "signal";
  }
  return documentIdOf(item) ? "documents" : null;
}

/**
 * Ошибка решения: 409 «уже решено» — строка уходит с сообщением (`gone`); 409 «нельзя
 * применить» и 422 — сообщение у предложения, статус прежний.
 */
export function decisionError(error: unknown): { text: string; gone: boolean } {
  // 404 — пациента передали другому врачу: предложение больше не его, строка уходит.
  const status = (error as { status?: unknown } | null)?.status;
  if (status === 404 || detailOf(error) === NOT_FOUND) {
    return { text: "Предложение больше недоступно", gone: true };
  }
  const { text, stale } = decisionErrorText(error);
  return { text: upperFirst(text), gone: stale };
}

/** Чем кончилось решение: подтверждено, отклонено или предложение ушло (409, 404). */
export type Outcome = { kind: "confirmed" | "rejected" } | { kind: "gone"; text: string };

/**
 * Выбор после решения. Решённое ещё выбрано — следующее (`nextAfterDecision`), `moved`.
 * Врач успел выбрать другую строку, пока решение было в обработке, — выбор не меняется.
 */
export function selectionAfterDecision(
  groups: readonly PatientGroup[],
  selectedId: string | null,
  decidedId: string,
): { selectedId: string | null; moved: boolean } {
  if (selectedId !== decidedId) {
    return { selectedId, moved: false };
  }
  return { selectedId: nextAfterDecision(groups, decidedId), moved: true };
}

/**
 * Сообщение о решении в `role="status"`. Подтверждение и отклонение — для чтения с экрана:
 * на экране уже следующее предложение. Ушедшее (409, 404) видно словами, если врач
 * смотрел именно его.
 */
export function decisionNotice(
  outcome: Outcome,
  title: string,
  moved: boolean,
): { text: string; visible: boolean } {
  if (outcome.kind === "gone") {
    return { text: outcome.text, visible: moved };
  }
  const word = outcome.kind === "confirmed" ? "Подтверждено" : "Отклонено";
  return { text: `${word}: ${title}`, visible: false };
}

const NOT_FOUND = "Proposal not found";
const REGISTERED = "найден в ГРЛС";
const PROFILE_CHECKS: readonly string[] = ["indication", "age"];
const ROW_ICON: Record<string, ApplicabilityIcon> = { indication: "indication", age: "profile" };

function prescriptionRows(p: Payload): ApplicabilityRow[] {
  const checks = checksOf(p);
  const title = typeof p.indication_title === "string" ? p.indication_title : null;
  return PROFILE_CHECKS.flatMap((id): ApplicabilityRow[] => {
    const check = checks.find((c) => c.id === id);
    const text = id === "indication" && title ? `Показание: ${title}` : check?.title;
    if (!text) {
      return [];
    }
    return [
      {
        icon: ROW_ICON[id] ?? "profile",
        text,
        detail: check?.detail ? upperFirst(check.detail) : null,
        warning: check !== undefined && check.status !== "pass",
      },
    ];
  });
}

function relationKey(item: QueueItem): string | null {
  if (item.kind === FINDING) {
    const analysis = item.payload.analysis_id;
    return typeof analysis === "string" && analysis ? `analysis:${analysis}` : null;
  }
  const document = documentIdOf(item);
  return document ? `document:${document}` : null;
}

function casePage(p: Payload): string {
  return typeof p.page === "number" ? `История болезни, стр. ${p.page}` : "История болезни";
}

function checksOf(p: Payload): Check[] {
  return Array.isArray(p.checks) ? (p.checks as Check[]) : [];
}

type Link = { source: string; title: string; locator: string; url?: string };

function isLink(value: unknown): value is Link {
  return typeof value === "object" && value !== null && "source" in value && "title" in value;
}

function toRef(link: Link): SourceRef {
  const ref: SourceRef = { source: link.source, title: link.title, locator: link.locator };
  if (link.url) {
    ref.url = link.url;
  }
  return ref;
}

/** «19.10.2026» в часовом поясе браузера. */
function dateText(iso: string): string {
  const date = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(date.getDate())}.${pad(date.getMonth() + 1)}.${date.getFullYear()}`;
}

export function upperFirst(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function lowerFirst(text: string): string {
  return text.charAt(0).toLowerCase() + text.slice(1);
}
