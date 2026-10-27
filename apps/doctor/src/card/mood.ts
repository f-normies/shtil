/**
 * График настроения: период, агрегация, геометрия, подписи оси, пропуски и скрытая таблица
 * (спека #115, разделы 3.3 и 3.4). Правила — аннотация «График настроения: правила для
 * разработки» (`docs/design/figma/doctor/README.md`). Чистые функции, без React.
 *
 * Порогов и цветов сигналов у графика нет: шкала настроения не валидирована (#93, п. 7).
 */
import type { Schemas } from "@compass/api-client";
import { curveMonotoneX, line } from "d3-shape";
import {
  addDays,
  type Day,
  dayMonth,
  dayMonthShort,
  dayNumber,
  dayOf,
  daysBetween,
  isDay,
  monthOf,
  shortDate,
  timeOf,
  weekdayLong,
  weekdayShort,
} from "./days";

/** Отметка настроения периода (`GET /patients/{id}/diary/moods`, #31). */
export type MoodPoint = Schemas["DiaryMoodPoint"];

export const PRESETS = [
  { days: 7, label: "Неделя" },
  { days: 14, label: "2 недели" },
  { days: 30, label: "Месяц" },
  { days: 90, label: "3 месяца" },
] as const;
export const DEFAULT_DAYS = 14;
export const MIN_DAYS = 7;
export const MAX_DAYS = 90;
/** С этой длины точка графика — среднее за неделю. */
const WEEKLY_FROM = 32;

const LEVEL_WORDS = ["Очень тяжело", "Тяжело", "Ровно", "Неплохо", "Хорошо"];

/** Вид отметки: у ежедневной есть её день `daily_on`, у быстрой и у записи с настроением нет. */
export const markKind = (mark: MoodPoint): string =>
  mark.daily_on === null ? "Отметка настроения" : "Ежедневная отметка";

/** Поле графика 608 × 184: подписи уровней 0–96, данные 104–608, дорожки через 32 px. */
export const CHART = {
  width: 608,
  height: 184,
  left: 104,
  plot: 504,
  top: 8,
  bottom: 136,
  track: 32,
  dateY: 152,
  weekdayY: 168,
} as const;
const MIN_DOT_STEP = 24;
const LABEL_GAP = 8;
/** Средняя ширина знака подписи «Сноска» 12 px. Замерять текст в чистой функции нечем. */
const CHAR_WIDTH = 6;

export type PeriodSearch = { period?: number; from?: Day; to?: Day };

/** Период графика. `observedFrom` — первый день наблюдения внутри периода. */
export type Period = {
  start: Day;
  end: Day;
  days: number;
  preset: number | null;
  observedFrom: Day;
};

/** Точка графика: день или неделя. `value` — среднее, `null` — пропуск. */
export type Slot = {
  index: number;
  start: Day;
  end: Day;
  days: number;
  marks: MoodPoint[];
  value: number | null;
  markedDays: number;
  observed: boolean;
};

export type ChartPoint = { index: number; x: number; y: number; slot: Slot };
export type Zone = { x1: number; x2: number; label: string; inside: boolean };
export type AxisLabel = {
  index: number;
  x: number;
  text: string;
  weekday: string | null;
  today: boolean;
};
export type TableRow = { entryId: string; date: string; time: string; level: string; kind: string };

const isPreset = (days: number) => PRESETS.some((p) => p.days === days);
const mean = (values: readonly number[]) => values.reduce((a, b) => a + b, 0) / values.length;
const later = (a: Day, b: Day) => (a > b ? a : b);

/** Параметры адреса: `?period=14` или `?from=2026-09-28&to=2026-10-11`. */
export function parsePeriodSearch(search: Record<string, unknown>): PeriodSearch {
  const { from, to } = search;
  if (typeof from === "string" && typeof to === "string" && isDay(from) && isDay(to)) {
    return { from, to };
  }
  const period = Number(search.period);
  return isPreset(period) ? { period } : {};
}

/** Свой период: от 7 до 90 дней, не раньше начала наблюдения и не позже сегодня. */
export function isValidRange(from: Day, to: Day, today: Day, observationStart: Day): boolean {
  const days = daysBetween(from, to) + 1;
  return from >= observationStart && to <= today && days >= MIN_DAYS && days <= MAX_DAYS;
}

/** Период из адреса. Неверный период заменяется пресетом по умолчанию — 2 недели. */
export function resolvePeriod(search: PeriodSearch, today: Day, observationStart: Day): Period {
  const { from, to } = search;
  if (from && to && isValidRange(from, to, today, observationStart)) {
    return {
      start: from,
      end: to,
      days: daysBetween(from, to) + 1,
      preset: null,
      observedFrom: from,
    };
  }
  const days = search.period && isPreset(search.period) ? search.period : DEFAULT_DAYS;
  const start = addDays(today, -(days - 1));
  const observation = observationStart < today ? observationStart : today;
  return { start, end: today, days, preset: days, observedFrom: later(start, observation) };
}

/**
 * Дни для API дневника (#31): `start` и `end` включительно. Сервер принимает 7–90 дней не
 * раньше начала наблюдения, поэтому часть пресета до наблюдения не запрашивается. Меньше
 * 7 дней наблюдения запросить нельзя: `null`.
 */
export function periodQuery(period: Period): { start: Day; end: Day } | null {
  const days = daysBetween(period.observedFrom, period.end) + 1;
  return days >= MIN_DAYS ? { start: period.observedFrom, end: period.end } : null;
}

/** День, с которого API отдаёт дневник пациента: 7-й день наблюдения. */
export const diaryOpensOn = (observationStart: Day): Day => addDays(observationStart, MIN_DAYS - 1);

/** «Показать неделю» из поповера: 7 дней, кончаются концом недели; в первой — от начала наблюдения. */
export function weekPeriod(
  slot: Slot,
  today: Day,
  observationStart: Day,
): { from: Day; to: Day } | null {
  const from = later(addDays(slot.end, -(MIN_DAYS - 1)), observationStart);
  const to = addDays(from, MIN_DAYS - 1);
  return isValidRange(from, to, today, observationStart) ? { from, to } : null;
}

export const isWeekly = (period: Period): boolean => period.days >= WEEKLY_FROM;

/**
 * Точки периода. До 31 дня — по дню, среднее отметок дня. Дольше — по неделе: недели
 * отсчитываются назад от конца периода, первая может быть короче; значение недели —
 * среднее её дней, чтобы день с несколькими отметками не перевешивал остальные.
 */
export function buildSlots(period: Period, marks: readonly MoodPoint[]): Slot[] {
  const byDay = new Map<Day, MoodPoint[]>();
  const sorted = [...marks].sort((a, b) => Date.parse(a.occurred_at) - Date.parse(b.occurred_at));
  for (const m of sorted) {
    const day = dayOf(m.occurred_at);
    if (day >= period.start && day <= period.end) {
      byDay.set(day, [...(byDay.get(day) ?? []), m]);
    }
  }
  const size = isWeekly(period) ? 7 : 1;
  const count = Math.ceil(period.days / size);
  return Array.from({ length: count }, (_, index) => {
    const end = addDays(period.end, -(count - 1 - index) * size);
    const start = later(period.start, addDays(end, -(size - 1)));
    const days = daysBetween(start, end) + 1;
    const lists = Array.from({ length: days }, (_, i) => byDay.get(addDays(start, i)) ?? []);
    const dayValues = lists
      .filter((l) => l.length > 0)
      .map((l) => mean(l.map((m) => m.mood_level)));
    return {
      index,
      start,
      end,
      days,
      marks: lists.flat(),
      value: dayValues.length > 0 ? mean(dayValues) : null,
      markedDays: dayValues.length,
      observed: end >= period.observedFrom,
    };
  });
}

/** Центр точки: x = 104 + (i + 0,5) × 504 / число точек. */
export const slotX = (index: number, count: number): number =>
  CHART.left + ((index + 0.5) * CHART.plot) / count;

/** Дорожка уровня: «Хорошо» сверху (y = 8), «Очень тяжело» снизу (y = 136). */
export const levelY = (value: number): number => CHART.top + (5 - value) * CHART.track;

/** Обычные точки рисуются, только если на точку приходится не меньше 24 px. */
export const showsDots = (count: number): boolean => CHART.plot / count >= MIN_DOT_STEP;

export function chartPoints(slots: readonly Slot[]): ChartPoint[] {
  return slots.flatMap((slot) =>
    slot.observed && slot.value !== null
      ? [{ index: slot.index, x: slotX(slot.index, slots.length), y: levelY(slot.value), slot }]
      : [],
  );
}

/** Кривая — монотонная кубическая (d3 curveMonotoneX). Одна точка — без кривой. */
export function curvePath(points: readonly ChartPoint[]): string | null {
  if (points.length < 2) {
    return null;
  }
  return line<ChartPoint>(
    (p) => p.x,
    (p) => p.y,
  ).curve(curveMonotoneX)(points);
}

/** Участки кривой через пропуск — пунктир. Пропуски в начале и в конце не рисуются. */
export function gapSpans(points: readonly ChartPoint[]): { x1: number; x2: number }[] {
  return points.flatMap((p, i) => {
    const prev = points[i - 1];
    return prev && p.index - prev.index > 1 ? [{ x1: prev.x, x2: p.x }] : [];
  });
}

/** Зона до начала наблюдения — целые точки; подпись внутри, если зона шире 150 px. */
export function observationZone(period: Period, slots: readonly Slot[]): Zone | null {
  const hidden = slots.filter((s) => !s.observed).length;
  if (hidden === 0) {
    return null;
  }
  const x2 = CHART.left + (hidden * CHART.plot) / slots.length;
  return {
    x1: CHART.left,
    x2,
    label: `Наблюдение с ${shortDate(period.observedFrom)}`,
    inside: x2 - CHART.left > 150,
  };
}

/**
 * Подписи оси. До 14 дней — каждый день с днём недели, до 31 — каждый третий день от
 * сегодня назад, дольше — каждая неделя, число её первого дня. У первой подписи и у первой
 * подписи нового месяца — сокращение месяца. Если между подписями меньше 8 px, шаг вдвое
 * больше. `step` — пикселей на точку; по умолчанию — по полю графика.
 */
export function axisLabels(
  slots: readonly Slot[],
  today: Day,
  step = CHART.plot / slots.length,
): AxisLabel[] {
  const count = slots.length;
  const weekly = (slots.at(-1)?.days ?? 1) > 1;
  const withWeekday = !weekly && count <= 14;
  let stride = !weekly && count > 14 ? 3 : 1;
  for (;;) {
    const labels = pickLabels(slots, stride, withWeekday, today);
    if (stride >= count || fits(labels, step)) {
      return labels;
    }
    stride *= 2;
  }
}

function pickLabels(
  slots: readonly Slot[],
  stride: number,
  withWeekday: boolean,
  today: Day,
): AxisLabel[] {
  const chosen = slots.filter((s) => (slots.length - 1 - s.index) % stride === 0);
  return chosen.map((slot, i) => {
    const prev = chosen[i - 1];
    const newMonth = !prev || monthOf(prev.start) !== monthOf(slot.start);
    return {
      index: slot.index,
      x: slotX(slot.index, slots.length),
      text: newMonth ? dayMonthShort(slot.start) : dayNumber(slot.start),
      weekday: withWeekday ? weekdayShort(slot.start) : null,
      today: slot.start <= today && today <= slot.end,
    };
  });
}

function fits(labels: readonly AxisLabel[], step: number): boolean {
  return labels.every((label, i) => {
    const prev = labels[i - 1];
    if (!prev) {
      return true;
    }
    const halfWidths = ((prev.text.length + label.text.length) * CHAR_WIDTH) / 2;
    return (label.index - prev.index) * step - halfWidths >= LABEL_GAP;
  });
}

/** Уровень словом. Среднее между уровнями — «Между «Тяжело» и «Ровно»». */
export function levelWord(value: number): string {
  const rounded = Math.round(value);
  if (Math.abs(value - rounded) < 1e-9) {
    return LEVEL_WORDS[rounded - 1] ?? "";
  }
  const low = LEVEL_WORDS[Math.floor(value) - 1] ?? "";
  const high = LEVEL_WORDS[Math.ceil(value) - 1] ?? "";
  return `Между «${low}» и «${high}»`;
}

/**
 * Полоса уровня для подсчётов: уровень, если среднее ровно на нём, иначе «между» двумя
 * соседними (n + 0,5). Среднее 2,5 — не «Ровно»: вверх его не округляем.
 */
export const levelBand = (value: number): number =>
  Number.isInteger(value) ? value : Math.floor(value) + 0.5;

/** Уровень последней точки для выделения его подписи; среднее между уровнями — без выделения. */
export function currentLevel(points: readonly ChartPoint[]): number | null {
  const value = points.at(-1)?.slot.value ?? null;
  return value !== null && Number.isInteger(value) ? value : null;
}

/** День с днём недели: «16 октября, пятница». */
export const dayWithWeekday = (day: Day): string => `${dayMonth(day)}, ${weekdayLong(day)}`;

/** Скрытая таблица для чтения с экрана: каждая отметка периода, старые первыми. */
export function tableRows(slots: readonly Slot[]): TableRow[] {
  return slots
    .flatMap((s) => s.marks)
    .map((m) => ({
      entryId: m.entry_id,
      date: dayWithWeekday(dayOf(m.occurred_at)),
      time: timeOf(m.occurred_at),
      level: levelWord(m.mood_level),
      kind: markKind(m),
    }));
}

/** Клавиатура графика: ← → по точкам с отметкой, Home и End — первая и последняя. */
export function moveActive(
  points: readonly ChartPoint[],
  current: number | null,
  key: string,
): number | null {
  const indexes = points.map((p) => p.index);
  const first = indexes[0];
  const last = indexes.at(-1);
  if (first === undefined || last === undefined) {
    return null;
  }
  if (key === "Home") {
    return first;
  }
  if (key === "End") {
    return last;
  }
  if (key !== "ArrowRight" && key !== "ArrowLeft") {
    return current;
  }
  // Фокус только пришёл на график: стрелки начинают с последней отметки.
  if (current === null) {
    return last;
  }
  return key === "ArrowRight"
    ? (indexes.find((i) => i > current) ?? current)
    : (indexes.findLast((i) => i < current) ?? current);
}
