/**
 * Поповер календаря дневника (кадр 06г; «Врач v4/Поповер календаря» и «…/День»): два месяца,
 * первый щелчок — начало, второй — конец. Период — от 7 до 90 дней, от начала наблюдения
 * до сегодня. Чистые функции, без React.
 */
import { addDays, type Day, dayMonth, daysBetween, plural, rangeText, weekdayIndex } from "./days";
import { MAX_DAYS, MIN_DAYS } from "./mood";

/** Месяц `YYYY-MM`. */
export type Month = string;
export type Selection = { start: Day | null; end: Day | null };
export type Limits = { today: Day; observationStart: Day };
export type DayState = {
  disabled: boolean;
  start: boolean;
  end: boolean;
  inRange: boolean;
  today: boolean;
};

const MONTH_NAMES = [
  "Январь",
  "Февраль",
  "Март",
  "Апрель",
  "Май",
  "Июнь",
  "Июль",
  "Август",
  "Сентябрь",
  "Октябрь",
  "Ноябрь",
  "Декабрь",
];

const pad = (n: number) => String(n).padStart(2, "0");
const yearMonth = (month: Month): [number, number] => {
  const [y, m] = month.split("-").map(Number);
  return [y ?? 0, m ?? 1];
};
const daysInMonth = (month: Month) => {
  const [y, m] = yearMonth(month);
  return new Date(Date.UTC(y, m, 0)).getUTCDate();
};
const clamp = (day: Day, { today, observationStart }: Limits): Day => {
  if (day < observationStart) {
    return observationStart;
  }
  return day > today ? today : day;
};

export const monthOfDay = (day: Day): Month => day.slice(0, 7);

export function addMonths(month: Month, n: number): Month {
  const [y, m] = yearMonth(month);
  const total = y * 12 + (m - 1) + n;
  return `${Math.floor(total / 12)}-${pad((total % 12) + 1)}`;
}

export function monthTitle(month: Month): string {
  const [y, m] = yearMonth(month);
  return `${MONTH_NAMES[m - 1]} ${y}`;
}

/** Недели месяца с понедельника; пустые ячейки — сдвиг первой и последней недели. */
export function monthWeeks(month: Month): (Day | null)[][] {
  const first = `${month}-01`;
  const offset = (weekdayIndex(first) + 6) % 7;
  const cells: (Day | null)[] = [
    ...Array<null>(offset).fill(null),
    ...Array.from({ length: daysInMonth(month) }, (_, i) => addDays(first, i)),
  ];
  while (cells.length % 7 !== 0) {
    cells.push(null);
  }
  return Array.from({ length: cells.length / 7 }, (_, w) => cells.slice(w * 7, w * 7 + 7));
}

/**
 * После листания месяцев кнопками фокусируемый день остаётся в видимой сетке: то же число
 * в ближайшем видимом месяце, в пределах наблюдения. Иначе Tab не найдёт день в сетке.
 */
export function focusInView(focused: Day, right: Month, limits: Limits): Day {
  const left = addMonths(right, -1);
  const month = monthOfDay(focused);
  if (month >= left && month <= right) {
    return clamp(focused, limits);
  }
  const target = month > right ? right : left;
  const date = Math.min(Number(focused.slice(8)), daysInMonth(target));
  return clamp(`${target}-${pad(date)}`, limits);
}

/** Стрелки листают по месяцу и неактивны на месяцах начала наблюдения и сегодня. */
export function canPage(month: Month, direction: -1 | 1, limits: Limits): boolean {
  return direction < 0
    ? month > monthOfDay(limits.observationStart)
    : month < monthOfDay(limits.today);
}

/**
 * Состояние дня. Недоступны дни до начала наблюдения и после сегодня. Когда выбрано только
 * начало, недоступны и дни, с которыми период выйдет короче 7 или длиннее 90 дней; дни
 * раньше начала доступны: щелчок по ним переносит начало.
 */
export function dayState(day: Day, selection: Selection, limits: Limits): DayState {
  const { start, end } = selection;
  let disabled = day < limits.observationStart || day > limits.today;
  if (start && !end && day > start) {
    const length = daysBetween(start, day) + 1;
    disabled ||= length < MIN_DAYS || length > MAX_DAYS;
  }
  return {
    disabled,
    start: day === start,
    end: day === end,
    inRange: Boolean(start && end && day > start && day < end),
    today: day === limits.today,
  };
}

/** Щелчок по дню: начало, затем конец; щелчок раньше начала или после выбора — новое начало. */
export function pickDay(selection: Selection, day: Day, limits: Limits): Selection {
  if (dayState(day, selection, limits).disabled) {
    return selection;
  }
  const { start, end } = selection;
  if (!start || end || day <= start) {
    return { start: day, end: null };
  }
  return { start, end: day };
}

/** Строка под календарём: что выбрано и сколько дней. */
export function selectionText({ start, end }: Selection): string {
  if (!start) {
    return "Выберите начало периода";
  }
  if (!end) {
    return `Начало — ${dayMonth(start)}, выберите конец`;
  }
  const days = daysBetween(start, end) + 1;
  return `${rangeText(start, end)}, ${days} ${plural(days, "день", "дня", "дней")}`;
}

const DAY_STEPS: Record<string, number> = {
  ArrowLeft: -1,
  ArrowRight: 1,
  ArrowUp: -7,
  ArrowDown: 7,
};

/** Клавиатура: стрелки — по дням, PageUp и PageDown — по месяцам; не выходит за наблюдение. */
export function moveDay(day: Day, key: string, limits: Limits): Day {
  const step = DAY_STEPS[key];
  if (step !== undefined) {
    return clamp(addDays(day, step), limits);
  }
  if (key === "PageUp" || key === "PageDown") {
    const month = addMonths(monthOfDay(day), key === "PageUp" ? -1 : 1);
    const date = Math.min(Number(day.slice(8)), daysInMonth(month));
    return clamp(`${month}-${pad(date)}`, limits);
  }
  return day;
}
