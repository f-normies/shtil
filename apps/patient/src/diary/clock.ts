import type { DiaryContextCacheRecord } from "./types";

export type DiaryClock = {
  occurredAt: string;
  today: string;
  timezone: string;
  source: "cached-context" | "device";
};

const DEFAULT_TIMEZONE = "Europe/Moscow";

function isTimezone(value: string): boolean {
  try {
    new Intl.DateTimeFormat("en", { timeZone: value });
    return true;
  } catch {
    return false;
  }
}

export function diaryDateInTimezone(timestamp: string | number, timezone: string): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date(timestamp));
  const part = (type: "year" | "month" | "day") =>
    parts.find((value) => value.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")}`;
}

function dateAtNoon(date: string): Date {
  return new Date(`${date}T12:00:00.000Z`);
}

function isoDate(date: Date): string {
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(date.getUTCDate()).padStart(2, "0")}`;
}

/** Return the seven-day diary strip: two dates before today, today, then four dates after. */
export function diaryWeekDays(today: string): string[] {
  const start = dateAtNoon(today);
  start.setUTCDate(start.getUTCDate() - 2);
  return Array.from({ length: 7 }, (_, index) => {
    const day = new Date(start);
    day.setUTCDate(start.getUTCDate() + index);
    return isoDate(day);
  });
}

/** Format the same seven-day window shown by the diary strip. */
export function diaryWeekRange(today: string): string {
  const dates = diaryWeekDays(today);
  const start = dateAtNoon(dates[0] ?? today);
  const end = dateAtNoon(dates[dates.length - 1] ?? today);
  const formatter = new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "long",
    timeZone: "UTC",
  });
  const startLabel = formatter.format(start);
  const endLabel = formatter.format(end);
  const sameMonth =
    start.getUTCFullYear() === end.getUTCFullYear() && start.getUTCMonth() === end.getUTCMonth();
  if (sameMonth) return `${start.getUTCDate()}–${endLabel}`;

  const sameYear = start.getUTCFullYear() === end.getUTCFullYear();
  if (sameYear) return `${startLabel} – ${endLabel}`;

  const yearFormatter = new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
  return `${yearFormatter.format(start)} – ${yearFormatter.format(end)}`;
}

/** Resolve local event time from the last trusted server clock, then freeze it in the draft. */
export function resolveDiaryClock(
  cache: DiaryContextCacheRecord | null,
  deviceNow = Date.now(),
  fallbackTimezone = DEFAULT_TIMEZONE,
): DiaryClock {
  const context = cache?.context;
  const serverTime = context ? Date.parse(context.server_now) : Number.NaN;
  const fetchedAt = cache?.context_fetched_at ? Date.parse(cache.context_fetched_at) : Number.NaN;
  const timezone =
    context?.timezone && isTimezone(context.timezone)
      ? context.timezone
      : isTimezone(fallbackTimezone)
        ? fallbackTimezone
        : DEFAULT_TIMEZONE;
  const hasTrustedTime = Number.isFinite(serverTime) && Number.isFinite(fetchedAt);
  const occurredAtMs = hasTrustedTime ? serverTime + Math.max(0, deviceNow - fetchedAt) : deviceNow;

  return {
    occurredAt: new Date(occurredAtMs).toISOString(),
    today: diaryDateInTimezone(occurredAtMs, timezone),
    timezone,
    source: hasTrustedTime ? "cached-context" : "device",
  };
}
