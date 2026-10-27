/**
 * Сводка с прошлого приёма в «Обзоре» (спека #38, раздел 3.6; кадры «Врач v4 · 05б» и «05в»):
 * состояние группы, период, утверждения со ссылками на данные, правка в листе и ошибки
 * подтверждения. Чистые функции, без React. `timeZone` — для тестов, иначе пояс браузера.
 */
import type { Schemas } from "@compass/api-client";

type Summary = Schemas["VisitSummaryOut"];
type Fact = Schemas["SummaryFact"];
type Statement = Schemas["SummaryStatement"];
type Section = Fact["section"];

/**
 * Опрос раз в 3 с, пока черновик считается (спека #38, 3.6), и не дольше 3 минут, как у
 * справки по КР (#107): при выключенной модели скелетон не висит бесконечно, а каждый
 * запрос не пишет строку в журнал доступа.
 */
export const POLL_MS = 3_000;
export const POLL_LIMIT_MS = 180_000;

/** `slow` — опрос остановлен через 3 минуты, а черновик всё ещё считается. */
export type SummaryKind = "pending" | "slow" | "empty" | "failed" | "draft" | "stale" | "confirmed";

/** Значок утверждения: уровень сигнала или раздел, откуда факт. */
export type StatementIcon = "scale" | "mandatory" | "desirable" | "signal" | "diary" | "messages";

/** Раздел карточки, куда ведёт ссылка; `null` — раздела в кабинете ещё нет, ссылка — текстом. */
export type LinkTarget = "scales" | "signals" | null;

export type SourceLink = { label: string; target: LinkTarget };

/** Уровень сигнала словом: «Обязательно» и «Желательно» различимы не только цветом. */
export type LevelView = { tone: "mandatory" | "desirable"; word: string };

export type StatementView = {
  text: string;
  details: string;
  dates: string;
  icon: StatementIcon;
  level: LevelView | null;
  links: SourceLink[];
};

/** Ответ на ошибку запроса: текст для врача (`null` — молча) и нужно ли запросить сводку. */
export type ErrorOutcome = { text: string | null; refetch: boolean };

const LEVEL_WORDS = { mandatory: "Обязательно", desirable: "Желательно" } as const;

export type SummaryView = {
  kind: SummaryKind;
  period: string;
  notice: string | null;
  /** «Повторить»: снова поставить расчёт после сбоя (`POST …/summary/retry`). */
  retry: boolean;
  /** «Обновить»: запросить сводку снова и дать опросу ещё 3 минуты. */
  refresh: boolean;
  skeleton: boolean;
  empty: string | null;
  statements: StatementView[];
  canConfirm: boolean;
  editedNote: string | null;
};

const SECTIONS: Record<Section, { label: string; target: LinkTarget; icon: StatementIcon }> = {
  assessments: { label: "Шкалы", target: "scales", icon: "scale" },
  signals: { label: "Сигналы", target: "signals", icon: "signal" },
  diary: { label: "Дневник", target: null, icon: "diary" },
  messages: { label: "Сообщения", target: null, icon: "messages" },
};

/** «с 5 октября»: начало периода — подтверждение прошлой сводки или начало наблюдения. */
export function periodText(periodStart: string, timeZone?: string): string {
  return `с ${dayMonth(periodStart, timeZone)}`;
}

/**
 * Начало опроса: время первого ответа `pending`, полученного после открытия группы.
 * `at` — время этого ответа или `null`, пока показан ответ из кэша. Не `pending` — сброс.
 */
export function nextPollStart(
  since: number | null,
  summary: Summary | undefined,
  at: number | null,
): number | null {
  if (summary?.status !== "pending") {
    return null;
  }
  return since ?? at;
}

/** Опрос шёл 3 минуты, а черновик всё ещё считается: группа предлагает «Обновить». */
export function isSlow(summary: Summary | undefined, since: number | null, at: number): boolean {
  return summary?.status === "pending" && since !== null && at - since >= POLL_LIMIT_MS;
}

/**
 * `refetchInterval` запроса сводки: 3 с, пока черновик считается, свежий или поверх
 * прежнего. Готовый ответ, сбой (ждёт «Повторить») и 3 минуты опроса — без опроса.
 */
export function pollInterval(
  summary: Summary | undefined,
  since: number | null,
  at: number,
): number | false {
  return summary?.status === "pending" && !isSlow(summary, since, at) ? POLL_MS : false;
}

export function summaryView(
  summary: Summary,
  { slow = false, timeZone }: { slow?: boolean; timeZone?: string } = {},
): SummaryView {
  const base = kindOf(summary);
  const kind = base === "pending" && slow ? "slow" : base;
  const pending = summary.status === "pending";
  const statements =
    kind === "pending" || kind === "slow" || kind === "empty" || kind === "failed"
      ? []
      : summary.statements.map((s) => statementView(s, summary.facts, timeZone));
  return {
    kind,
    period: periodText(summary.period_start, timeZone),
    notice: noticeOf(kind, summary.status, slow),
    retry: summary.status === "failed",
    refresh: slow && pending && (kind === "slow" || kind === "stale"),
    skeleton: kind === "pending",
    empty:
      kind === "empty" ? `Нет новых данных ${periodText(summary.period_start, timeZone)}` : null,
    statements,
    canConfirm: kind === "draft",
    editedNote: kind === "confirmed" && summary.edited ? "Изменено врачом" : null,
  };
}

function kindOf(summary: Summary): SummaryKind {
  if (summary.stale) {
    return "stale";
  }
  if (summary.status === "done") {
    return "draft";
  }
  return summary.status;
}

function noticeOf(kind: SummaryKind, status: Summary["status"], slow: boolean): string | null {
  if (kind === "failed") {
    return "Не удалось составить сводку";
  }
  if (kind === "slow") {
    return "Сводка ещё составляется";
  }
  if (kind !== "stale") {
    return null;
  }
  if (status === "failed") {
    return "Не удалось обновить сводку по новым данным";
  }
  return slow
    ? "Данные пациента изменились, сводка ещё обновляется"
    : "Данные пациента изменились, сводка обновляется";
}

/** Утверждение: факты под ним, даты, значок и ссылки на разделы — по разделу каждого факта. */
export function statementView(
  statement: Statement,
  facts: readonly Fact[],
  timeZone?: string,
): StatementView {
  const own = statement.facts
    .map((n) => facts.find((f) => f.number === n))
    .filter((f): f is Fact => f !== undefined);
  const sections = [...new Set(own.map((f) => f.section))];
  const tone = levelOf(own);
  const first = own[0];
  return {
    text: statement.text,
    details: own.map((f) => f.text).join("; "),
    dates: datesText(own, timeZone),
    icon: tone ?? (first ? SECTIONS[first.section].icon : "signal"),
    level: tone ? { tone, word: LEVEL_WORDS[tone] } : null,
    links: sections.map((s) => ({ label: SECTIONS[s].label, target: SECTIONS[s].target })),
  };
}

/** Высший уровень сигналов утверждения из поля `level` факта; у других видов он пуст. */
function levelOf(facts: readonly Fact[]): LevelView["tone"] | null {
  const levels = facts.map((f) => f.level);
  if (levels.includes("mandatory")) {
    return "mandatory";
  }
  return levels.includes("desirable") ? "desirable" : null;
}

/** «19.10», «6–18.10» или «28.09–18.10». */
function datesText(facts: readonly Fact[], timeZone?: string): string {
  if (facts.length === 0) {
    return "";
  }
  const sorted = facts.map((f) => f.at).sort((a, b) => Date.parse(a) - Date.parse(b));
  const first = dayParts(sorted[0] ?? "", timeZone);
  const last = dayParts(sorted[sorted.length - 1] ?? "", timeZone);
  if (first.day === last.day && first.month === last.month) {
    return `${last.day}.${last.month}`;
  }
  if (first.month === last.month) {
    return `${first.day}–${last.day}.${last.month}`;
  }
  return `${first.day}.${first.month}–${last.day}.${last.month}`;
}

function dayParts(iso: string, timeZone?: string): { day: string; month: string } {
  const parts = new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "2-digit",
    timeZone,
  }).formatToParts(new Date(iso));
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((p) => p.type === type)?.value ?? "";
  return { day: part("day"), month: part("month") };
}

function dayMonth(iso: string, timeZone?: string): string {
  return new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", timeZone }).format(
    new Date(iso),
  );
}

/** «вы, 19.10, 09:46» или ФИО другого врача (кадр 05в). */
export function confirmedText(
  summary: Summary,
  currentDoctorId: string,
  timeZone?: string,
): string {
  const who =
    summary.confirmed_by === currentDoctorId ? "вы" : (summary.confirmed_by_name ?? "другой врач");
  if (!summary.confirmed_at) {
    return who;
  }
  const when = new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZone,
  }).format(new Date(summary.confirmed_at));
  return `${who}, ${when}`;
}

/** Утверждение в листе правки. `key` — номер в черновике: удаление не сдвигает остальные. */
export type EditItem = { key: number; text: string; facts: number[] };

export function editItems(statements: readonly Statement[]): EditItem[] {
  return statements.map((s, key) => ({ key, text: s.text, facts: [...s.facts] }));
}

export function updateItem(items: readonly EditItem[], key: number, text: string): EditItem[] {
  return items.map((item) => (item.key === key ? { ...item, text } : item));
}

/** Удалить утверждение можно, добавить новое — нет: без фактов его не проверить (спека, 7). */
export function removeItem(items: readonly EditItem[], key: number): EditItem[] {
  return items.filter((item) => item.key !== key);
}

/** Почему «Подтвердить сводку» в листе пока нельзя нажать (сервер ждёт 1–5 непустых). */
export function editHint(items: readonly EditItem[]): string | null {
  if (items.length === 0) {
    return "Оставьте хотя бы одно утверждение";
  }
  return items.some((item) => !item.text.trim())
    ? "Заполните текст каждого утверждения или удалите его"
    : null;
}

/** Тело `POST …/summary/{id}/confirm`: текст врача и номера фактов из черновика. */
export function confirmBody(items: readonly EditItem[]): Schemas["ConfirmSummaryIn"] {
  return { statements: items.map((item) => ({ text: item.text.trim(), facts: item.facts })) };
}

/**
 * Ошибка подтверждения. 409 `stale` — черновик устарел; 409 `already_confirmed` — сводку уже
 * подтвердили; 404 — сводки нет. 422 (`unknown_fact`, лишние утверждения и другие отказы
 * сервера) и незнакомый 409 — общий текст. Во всех этих случаях сводку запрашиваем заново.
 * Прочие ошибки — сбой связи: ввод врача сохраняется.
 */
export function confirmError(error: unknown): ErrorOutcome {
  const { status, code } = problemOf(error);
  if (status === 409 && code === "stale") {
    return {
      text: "Данные пациента изменились. Сводка обновляется, правку нужно будет повторить.",
      refetch: true,
    };
  }
  if ((status === 409 && code === "already_confirmed") || status === 404) {
    return { text: null, refetch: true };
  }
  if (status === 409 || status === 422) {
    return { text: "Не удалось подтвердить сводку, обновите её", refetch: true };
  }
  return { text: "Сводка не подтверждена. Проверьте связь и попробуйте ещё раз.", refetch: false };
}

/** Ошибка «Повторить». 409 `not_failed` — расчёт уже идёт или готов: просто запросить сводку. */
export function retryError(error: unknown): ErrorOutcome {
  return problemOf(error).code === "not_failed"
    ? { text: null, refetch: true }
    : { text: "Не удалось отправить запрос. Попробуйте ещё раз.", refetch: false };
}

/** Поля `status` и `code` тела ошибки RFC 9457; у сбоя сети их нет. */
function problemOf(error: unknown): { status: unknown; code: unknown } {
  if (typeof error !== "object" || error === null) {
    return { status: null, code: null };
  }
  const { status, code } = error as { status?: unknown; code?: unknown };
  return { status, code };
}
