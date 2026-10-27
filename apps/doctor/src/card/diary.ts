/**
 * Записи и находки вкладки «Дневник» в карточке врача (спека #115, разделы 3.5 и 3.6).
 * Формы — ручек #31: `DiaryDoctorEntryOut` (`GET /patients/{id}/diary`) и `DiaryAudioOut`.
 * Находки — предложения `freetext.finding` (#32): их `payload.source_id` — источник разбора
 * записи (текст, другое лекарство или расшифровка), а не сама запись.
 */
import type { Schemas } from "@compass/api-client";
import { addDays, type Day, dayMonth, dayOf, shortDate, timeOf, weekdayLong, yearOf } from "./days";
import { levelWord } from "./mood";
import { FINDING, newestFirst, proposalTitle } from "./proposals";

export type DiaryEntry = Schemas["DiaryDoctorEntryOut"];
export type EntryKind = DiaryEntry["kind"];
type Audio = Schemas["DiaryAudioOut"];
type Proposal = Schemas["ProposalOut"];

export type EntryFilter = "all" | "voice" | "text" | "mood";
export type DayGroup = { day: Day; title: string; entries: DiaryEntry[] };
export type FindingView = { title: string; category: string; record: string };
export type TranscriptStatus = "processing" | "done" | "failed";

export const ENTRY_FILTERS: { value: EntryFilter; label: string }[] = [
  { value: "all", label: "Все" },
  { value: "voice", label: "Голос" },
  { value: "text", label: "Текст" },
  { value: "mood", label: "Настроение" },
];

export const KIND_TITLES: Record<EntryKind, string> = {
  voice: "Голосовая запись",
  text: "Запись в дневнике",
  mood: "Отметка настроения",
  daily: "Ежедневная отметка",
};

/** Статусы расшифровки — спека #32. */
const TRANSCRIPT_TEXTS: Record<TranscriptStatus, string> = {
  processing: "Расшифровывается…",
  done: "",
  failed: "Не удалось разобрать, послушайте запись",
};

// Длинная запись — до 6 строк и «Показать полностью» (кадр 06). Строк без замера не
// сосчитать: длиннее этого числа знаков текст почти наверняка не помещается, как в 07в.
const LONG_TEXT = 360;

const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);
const lower = (text: string) => text.charAt(0).toLowerCase() + text.slice(1);

/**
 * Фильтр записей. «Голос» — всё, что пациент наговорил, и ежедневная отметка голосом тоже;
 * «Текст» — записи текстом; «Настроение» — всё, что несёт отметку настроения и есть на графике.
 */
export function matchesFilter(entry: DiaryEntry, filter: EntryFilter): boolean {
  switch (filter) {
    case "voice":
      return entry.audio !== null;
    case "text":
      return entry.kind === "text";
    case "mood":
      return entry.mood_level !== null;
    default:
      return true;
  }
}

/** Заголовок дня: «Сегодня, 19 октября», «Вчера, 18 октября», «Суббота, 17 октября». */
export function dayTitle(day: Day, today: Day): string {
  const date = yearOf(day) === yearOf(today) ? dayMonth(day) : `${dayMonth(day)} ${yearOf(day)}`;
  if (day === today) {
    return `Сегодня, ${date}`;
  }
  if (day === addDays(today, -1)) {
    return `Вчера, ${date}`;
  }
  return `${capitalize(weekdayLong(day))}, ${date}`;
}

/**
 * Записи по дням, новые выше. Фильтр скрывает другие виды, группы дней остаются. Пока есть
 * следующая страница (`partialLastDay`), последний день может продолжиться на ней: пустым
 * для фильтра он не показывается.
 */
export function groupByDay(
  entries: readonly DiaryEntry[],
  today: Day,
  filter: EntryFilter,
  partialLastDay = false,
): DayGroup[] {
  const groups = new Map<Day, DiaryEntry[]>();
  for (const e of [...entries].sort(
    (a, b) => Date.parse(b.occurred_at) - Date.parse(a.occurred_at),
  )) {
    const day = dayOf(e.occurred_at);
    groups.set(day, [...(groups.get(day) ?? []), e]);
  }
  const result = [...groups.entries()].map(([day, list]) => ({
    day,
    title: dayTitle(day, today),
    entries: list.filter((e) => matchesFilter(e, filter)),
  }));
  const last = result.at(-1);
  return partialLastDay && last?.entries.length === 0 ? result.slice(0, -1) : result;
}

/** Длительность голоса: «1:24». */
export function formatDuration(ms: number): string {
  const seconds = Math.round(ms / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

const REFUSED_PERIOD = "Период не подходит: выберите от 7 до 90 дней наблюдения, не позже сегодня";

/**
 * Текст сбоя загрузки. Период, который API не принял (422 `invalid_period`), повтор не
 * исправит: нужен другой период. Остальное — `fallback` и «Повторить».
 */
export function diaryFailure(error: unknown, fallback: string): { text: string; retry: boolean } {
  const code = typeof error === "object" && error !== null && "code" in error ? error.code : null;
  return code === "invalid_period"
    ? { text: REFUSED_PERIOD, retry: false }
    : { text: fallback, retry: true };
}

export type Located = { kind: "found"; entryId: string } | { kind: "more" } | { kind: "missing" };

/**
 * Где запись для «Открыть запись». Список идёт от новых к старым по `occurred_at`: если
 * загруженные уже старше её времени `at`, а её нет, запись недоступна — пациент её скрыл.
 * Время неизвестно — следующие страницы читаются, пока они есть.
 */
export function locateEntry(
  loaded: readonly DiaryEntry[],
  hasNextPage: boolean,
  find: (entries: readonly DiaryEntry[]) => DiaryEntry | undefined,
  at: string | null,
): Located {
  const found = find(loaded);
  if (found) {
    return { kind: "found", entryId: found.entry_id };
  }
  const oldest = loaded.at(-1);
  const passed =
    at !== null && oldest !== undefined && Date.parse(oldest.occurred_at) < Date.parse(at);
  return hasNextPage && !passed ? { kind: "more" } : { kind: "missing" };
}

/** Id строки записи: к ней ведут «Открыть запись» в поповере графика и запись находки. */
export const entryAnchor = (entryId: string): string => `entry-${entryId}`;

/** Запись голоса для врача: поток по сессии врача, cookie того же сайта уходит сам. */
export const audioUrl = (patientId: string, entryId: string): string =>
  `/api/v1/patients/${patientId}/diary/${entryId}/audio`;

/**
 * Что показать под плеером. `spoken` — слова пациента, их показывают цитатой. Готовая, но
 * пустая расшифровка — тоже «не удалось разобрать»: в записи не нашлось слов.
 */
export function transcriptView(audio: Audio): {
  status: TranscriptStatus;
  text: string;
  spoken: boolean;
} {
  if (audio.status === "pending" || audio.status === "processing") {
    return { status: "processing", text: TRANSCRIPT_TEXTS.processing, spoken: false };
  }
  const said = audio.transcript?.trim() ?? "";
  if (audio.status === "done" && said) {
    return { status: "done", text: said, spoken: true };
  }
  return { status: "failed", text: TRANSCRIPT_TEXTS.failed, spoken: false };
}

/** Текст пациента. У голосовой записи он показан, только если пациент исправил расшифровку. */
export function ownText(entry: DiaryEntry): string | null {
  if (!entry.text || entry.text === entry.audio?.transcript) {
    return null;
  }
  return entry.text;
}

/** Строки «ключ — значение» отметки: настроение словом, слова к нему, другое лекарство. */
export function entryRows(entry: DiaryEntry): { label: string; value: string }[] {
  const rows: { label: string; value: string }[] = [];
  if (entry.mood_level !== null) {
    rows.push({ label: "Настроение", value: levelWord(entry.mood_level) });
    if (entry.mood_words.length > 0) {
      rows.push({ label: "Слова к настроению", value: entry.mood_words.join(", ") });
    }
  }
  if (entry.other_medicine_text) {
    rows.push({ label: "Другое лекарство", value: entry.other_medicine_text });
  }
  return rows;
}

export const isLongText = (text: string): boolean => text.length > LONG_TEXT;

/** Находки разбора дневника, которые ждут решения врача, новые первыми (спека #32). */
export function diaryFindings(proposals: readonly Proposal[]): Proposal[] {
  return newestFirst(
    proposals.filter(
      (p) => p.kind === FINDING && p.status === "proposed" && p.payload.source_type === "diary",
    ),
  );
}

/**
 * Запись каждой находки среди загруженных: по источнику разбора. У записи их несколько —
 * текст, другое лекарство, расшифровка, а после правки ещё и источники прежних версий.
 * Удалённой или не загруженной записи в ответе нет: её находка остаётся без записи.
 */
export function findingEntries(
  findings: readonly Proposal[],
  entries: readonly DiaryEntry[],
): Map<string, DiaryEntry> {
  const bySource = new Map<string, DiaryEntry>();
  for (const e of entries) {
    for (const s of [...e.sources, ...e.source_statuses]) {
      bySource.set(s.source_id, e);
    }
    if (e.audio?.transcript_source_id) {
      bySource.set(e.audio.transcript_source_id, e);
    }
  }
  const byFinding = new Map<string, DiaryEntry>();
  for (const f of findings) {
    const sourceId = f.payload.source_id;
    const found = typeof sourceId === "string" ? bySource.get(sourceId) : undefined;
    if (found) {
      byFinding.set(f.id, found);
    }
  }
  return byFinding;
}

/** Находки каждой записи: к ним ведут ссылки «Предложение системы: …» в записи. */
export function findingsByEntry(
  findings: readonly Proposal[],
  entries: readonly DiaryEntry[],
): Map<string, Proposal[]> {
  const byFinding = findingEntries(findings, entries);
  const byEntry = new Map<string, Proposal[]>();
  for (const f of findings) {
    const id = byFinding.get(f.id)?.entry_id;
    if (id) {
      byEntry.set(id, [...(byEntry.get(id) ?? []), f]);
    }
  }
  return byEntry;
}

/** Время записи находки `recorded_at`, как в очереди (#96); у старых — время предложения. */
function findingTime(proposal: Proposal): string {
  const { recorded_at } = proposal.payload;
  return typeof recorded_at === "string" && recorded_at ? recorded_at : proposal.created_at;
}

/**
 * Можно ли открыть запись находки: она загружена или может быть на следующих страницах.
 * Запись не позже своей находки, поэтому находка до начала периода — запись вне его.
 */
export function canOpenFinding(
  proposal: Proposal,
  entry: DiaryEntry | undefined,
  hasNextPage: boolean,
  periodStart: Day,
): boolean {
  return entry !== undefined || (hasNextPage && dayOf(findingTime(proposal)) >= periodStart);
}

/** Строка находки: что найдено, категория и запись со временем записи. */
export function findingView(proposal: Proposal): FindingView {
  const category = proposalTitle(proposal);
  const { summary, transcript } = proposal.payload;
  const at = findingTime(proposal);
  const kind = transcript === true ? "голосовая запись" : "запись в дневнике";
  return {
    title: typeof summary === "string" && summary ? capitalize(summary) : category,
    category,
    record: `${kind} ${shortDate(dayOf(at))}, ${timeOf(at)}`,
  };
}

/** Ссылка из записи на её находку: «Предложение системы: нарушение сна». */
export function findingLabel(proposal: Proposal): string {
  const summary = proposal.payload.summary;
  const what = typeof summary === "string" && summary ? summary : lower(proposalTitle(proposal));
  return `Предложение системы: ${what}`;
}
