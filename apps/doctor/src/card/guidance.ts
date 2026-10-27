/**
 * Варианты по клиническим рекомендациям в «Терапии» (спека #107, раздел 6): выбор состояния
 * панели, подпись, опрос и строки утверждения. Чистые функции, без React.
 */
import type { Schemas } from "@compass/api-client";

type Guidance = Schemas["GuidanceOut"];
type Claim = Schemas["Claim"];
type Fragment = Schemas["FragmentRef"];
type Status = Guidance["status"] | undefined;

/** Решение 11: опрос раз в 5 с, пока справка считается, и не дольше 3 минут. */
export const POLL_MS = 5_000;
export const POLL_LIMIT_MS = 180_000;

export type GuidanceKind =
  | "no-diagnoses"
  | "pending"
  | "slow"
  | "done"
  | "empty"
  | "no-guidelines"
  | "stale"
  | "failed";

/** `progress` — идёт опрос; `waiting` — опрос остановлен, справка ещё считается. */
export type NoticeTone = "info" | "progress" | "waiting" | "warning" | "error";

/** `retry` — снова поставить расчёт (`POST …/guidance/retry`); `refresh` — запросить снова. */
export type GuidanceAction = "retry" | "refresh";

export type Notice = { text: string; tone: NoticeTone; action: GuidanceAction | null };

export type GuidanceView = {
  kind: GuidanceKind;
  notice: Notice | null;
  claims: Claim[];
  skeleton: boolean;
};

const STALE = "Справка по прежним данным пациента.";

/**
 * Состояние панели по ответу `GET …/therapy/guidance` (таблица раздела 6).
 *
 * `slow` — опрос шёл 3 минуты и остановлен (`isSlow`). Порядок проверок важен: нет диагнозов,
 * затем прежний ответ, затем расчёт, затем готовый ответ. Пометки стоят над утверждениями,
 * прежний ответ не скрывается.
 */
export function guidanceView(guidance: Guidance, slow: boolean): GuidanceView {
  const view = (kind: GuidanceKind, notice: Notice | null, claims: Claim[] = []) => ({
    kind,
    notice,
    claims,
    skeleton: kind === "pending",
  });
  const { status, claims } = guidance;
  if (status === null) {
    return view("no-diagnoses", {
      text: "Добавьте диагноз, чтобы получить варианты по клиническим рекомендациям.",
      tone: "info",
      action: null,
    });
  }
  if (guidance.stale && status === "failed") {
    const text = `${STALE} Новую посчитать не удалось`;
    return view("stale", { text, tone: "error", action: "retry" }, claims);
  }
  if (guidance.stale && status === "pending") {
    const text = `${STALE} ${slow ? "Новая ещё считается" : "Новая считается"}`;
    return view("stale", { text, tone: "warning", action: slow ? "refresh" : null }, claims);
  }
  if (status === "pending") {
    return slow
      ? view("slow", { text: "Справка ещё считается", tone: "waiting", action: "refresh" })
      : view("pending", {
          text: "Готовим справку по клиническим рекомендациям",
          tone: "progress",
          action: null,
        });
  }
  if (status === "failed") {
    return view("failed", {
      text: "Справку посчитать не удалось.",
      tone: "error",
      action: "retry",
    });
  }
  if (guidance.guidelines.length === 0) {
    const text = "По диагнозам пациента клинических рекомендаций в базе нет.";
    return view("no-guidelines", { text, tone: "info", action: null }, claims);
  }
  if (claims.length === 0) {
    return view("empty", { text: "В базе знаний ответа нет.", tone: "info", action: null });
  }
  return view("done", null, claims);
}

/** Подпись над утверждениями: назначить через систему можно только препараты справочника. */
export function guidanceCaption(drugs: readonly string[]): string {
  const head = "Справка по клиническим рекомендациям.";
  if (drugs.length === 0) {
    return `${head} Справочник препаратов системы пока пуст: назначить через систему ничего нельзя.`;
  }
  return `${head} Назначить через систему можно только препараты справочника: ${drugs.join(", ")}.`;
}

/**
 * Начало опроса: время первого ответа `pending`, полученного после открытия панели.
 * `at` — время этого ответа или `null`, пока показан ответ из кэша. Не `pending` — сброс.
 */
export function nextPollStart(since: number | null, status: Status, at: number | null) {
  if (status !== "pending") {
    return null;
  }
  return since ?? at;
}

/** Опрос шёл 3 минуты, а справка всё ещё считается: панель предлагает «Обновить». */
export function isSlow(status: Status, since: number | null, at: number): boolean {
  return status === "pending" && since !== null && at - since >= POLL_LIMIT_MS;
}

/** `refetchInterval` запроса справки: 5 с при `pending`, иначе и после 3 минут — без опроса. */
export function pollInterval(status: Status, since: number | null, at: number): number | false {
  return status === "pending" && !isSlow(status, since, at) ? POLL_MS : false;
}

/** «УУР A, УДД 1»; у фрагмента без уровней — `null`. */
export function levelsText(fragment: Fragment): string | null {
  return levels(fragment, "УУР", "УДД");
}

/** Те же уровни полностью — для чтения с экрана. */
export function levelsSpoken(fragment: Fragment): string | null {
  return levels(
    fragment,
    "уровень убедительности рекомендаций",
    "уровень достоверности доказательств",
  );
}

function levels(fragment: Fragment, uur: string, udd: string): string | null {
  const parts = [
    fragment.uur ? `${uur} ${fragment.uur}` : null,
    fragment.udd !== null ? `${udd} ${fragment.udd}` : null,
  ].filter((part): part is string => part !== null);
  return parts.length > 0 ? parts.join(", ") : null;
}

const SECTION_NUMBER = /^\d+(\.\d+)*$/;

/**
 * Строка источника «{документ}, {раздел}». КР — документ из `guidelines` профиля или фрагмент
 * с уровнями: название КР в манифесте — только название болезни, поэтому «КР «…»».
 * У ОХЛП раздел — номер (`4.2`) и название без номера; у КР название раздела уже с номером.
 */
export function sourceText(fragment: Fragment, guidelineIds: readonly string[]): string {
  const guideline = guidelineIds.includes(fragment.source_id) || levelsText(fragment) !== null;
  const document = guideline ? `КР «${fragment.title}»` : fragment.title;
  return `${document}, ${sectionText(fragment.section, fragment.section_title)}`;
}

function sectionText(section: string, title: string): string {
  if (!SECTION_NUMBER.test(section) || title.startsWith(section)) {
    return title || section;
  }
  return title ? `${section}. ${title}` : section;
}

export type ClaimGroup = { key: string; claim: Claim; statements: string[] };

/**
 * Утверждения с одной цитатой одного фрагмента — одной группой (M-6, D3-4): модель цитирует
 * один абзац в нескольких утверждениях, цитата показана один раз. Порядок — по первому
 * появлению; `key` различает группы в списке. Повтор утверждения не дублируется.
 */
export function groupClaims(claims: readonly Claim[]): ClaimGroup[] {
  const groups = new Map<string, ClaimGroup>();
  for (const claim of claims) {
    const key = `${claim.fragment.id}/${claim.quote}`;
    const group = groups.get(key);
    if (!group) {
      groups.set(key, { key, claim, statements: [claim.statement] });
    } else if (!group.statements.includes(claim.statement)) {
      group.statements.push(claim.statement);
    }
  }
  return [...groups.values()];
}

/** Около 6 строк цитаты в колонке текста панели: длиннее — свёрнута до 6 строк. */
export const LONG_QUOTE = 480;

export function isLongQuote(quote: string): boolean {
  return quote.length > LONG_QUOTE;
}

export type QuoteParts = { before: string; quote: string; after: string };

/** Цитата в тексте фрагмента для выделения. Сервер хранит её в написании текста. */
export function splitQuote(text: string, quote: string): QuoteParts | null {
  const start = quote ? text.indexOf(quote) : -1;
  if (start < 0) {
    return null;
  }
  const end = start + quote.length;
  return { before: text.slice(0, start), quote, after: text.slice(end) };
}

/** «Посчитано 19.10, 09:40» в часовом поясе браузера (`timeZone` — для тестов). */
export function computedText(iso: string, timeZone?: string): string {
  const format = new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZone,
  });
  return `Посчитано ${format.format(new Date(iso))}`;
}
