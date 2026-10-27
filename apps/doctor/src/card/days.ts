/**
 * Календарные дни дневника (спека #115, решение 3.2). День — дата `YYYY-MM-DD` в часовом
 * поясе сервера: так сутки считает API #31 (`settings.timezone`), и у врача в другом поясе
 * границы периода совпадают с серверными. Время отметок — в том же поясе.
 * Арифметика дат идёт в UTC, поэтому переход на летнее время её не сбивает.
 */
export type Day = string;

/** Пояс сервера (`settings.timezone` в API). Сменится там — меняется здесь. */
export const SERVER_TIME_ZONE = "Europe/Moscow";

// Порядок частей у `formatToParts` не зависит от локали: части берутся по типу.
const SERVER_CLOCK = new Intl.DateTimeFormat("en-GB", {
  timeZone: SERVER_TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

const MS_PER_DAY = 86_400_000;
const DAY_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;

const MONTHS = [
  "января",
  "февраля",
  "марта",
  "апреля",
  "мая",
  "июня",
  "июля",
  "августа",
  "сентября",
  "октября",
  "ноября",
  "декабря",
];
// Сокращения месяцев в подписях оси — как в кадрах: «6 окт», «22 сен». Intl даёт «сент.».
const MONTHS_SHORT = [
  "янв",
  "фев",
  "мар",
  "апр",
  "мая",
  "июн",
  "июл",
  "авг",
  "сен",
  "окт",
  "ноя",
  "дек",
];
const WEEKDAYS_SHORT = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
const WEEKDAYS = [
  "воскресенье",
  "понедельник",
  "вторник",
  "среда",
  "четверг",
  "пятница",
  "суббота",
];

const pad = (n: number) => String(n).padStart(2, "0");

function parts(day: Day): [number, number, number] {
  const [, y, m, d] = DAY_PATTERN.exec(day) ?? [];
  return [Number(y), Number(m), Number(d)];
}

function utc(day: Day): number {
  const [y, m, d] = parts(day);
  return Date.UTC(y, m - 1, d);
}

function fromUtc(ms: number): Day {
  const date = new Date(ms);
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`;
}

function serverClock(at: string | Date): Record<string, string> {
  const date = typeof at === "string" ? new Date(at) : at;
  return Object.fromEntries(SERVER_CLOCK.formatToParts(date).map((p) => [p.type, p.value]));
}

/** День момента по поясу сервера. */
export function dayOf(at: string | Date): Day {
  const { year, month, day } = serverClock(at);
  return `${year}-${month}-${day}`;
}

export function addDays(day: Day, n: number): Day {
  return fromUtc(utc(day) + n * MS_PER_DAY);
}

/** Сколько дней от `from` до `to`: 6.10 → 19.10 — 13. */
export function daysBetween(from: Day, to: Day): number {
  return Math.round((utc(to) - utc(from)) / MS_PER_DAY);
}

/** Строка — существующая дата `YYYY-MM-DD`. */
export function isDay(value: string): boolean {
  return DAY_PATTERN.test(value) && fromUtc(utc(value)) === value;
}

export const monthOf = (day: Day): number => parts(day)[1] - 1;
export const yearOf = (day: Day): number => parts(day)[0];
export const dayNumber = (day: Day): string => String(parts(day)[2]);
export const dayMonth = (day: Day): string => `${dayNumber(day)} ${MONTHS[monthOf(day)]}`;
export const dayMonthShort = (day: Day): string =>
  `${dayNumber(day)} ${MONTHS_SHORT[monthOf(day)]}`;
export const shortDate = (day: Day): string => `${pad(parts(day)[2])}.${pad(parts(day)[1])}`;
export const weekdayIndex = (day: Day): number => new Date(utc(day)).getUTCDay();
export const weekdayShort = (day: Day): string => WEEKDAYS_SHORT[weekdayIndex(day)] ?? "";
export const weekdayLong = (day: Day): string => WEEKDAYS[weekdayIndex(day)] ?? "";

/** Время момента по поясу сервера: «09:05». */
export function timeOf(at: string): string {
  const { hour, minute } = serverClock(at);
  return `${hour}:${minute}`;
}

/** Период словами: «6–19 октября», «20 сентября — 19 октября»; годы — только если разные. */
export function rangeText(start: Day, end: Day): string {
  if (yearOf(start) !== yearOf(end)) {
    return `${dayMonth(start)} ${yearOf(start)} — ${dayMonth(end)} ${yearOf(end)}`;
  }
  if (monthOf(start) === monthOf(end)) {
    return `${dayNumber(start)}–${dayMonth(end)}`;
  }
  return `${dayMonth(start)} — ${dayMonth(end)}`;
}

/** Склонение по числу: 1 день, 2 дня, 5 дней. */
export function plural(n: number, one: string, few: string, many: string): string {
  const tens = n % 100;
  const units = n % 10;
  if (tens >= 11 && tens <= 14) {
    return many;
  }
  if (units === 1) {
    return one;
  }
  return units >= 2 && units <= 4 ? few : many;
}
