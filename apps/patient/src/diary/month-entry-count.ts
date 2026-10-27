import type { DiaryEntryRecord } from "./types";

function dateInTimezone(timestamp: string, timezone: string): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date(timestamp));
  const part = (key: string) => parts.find((item) => item.type === key)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")}`;
}

/** Count server entries plus visible durable local entries not yet acknowledged. */
export function monthEntryCount(
  entries: readonly DiaryEntryRecord[],
  today: string,
  timezone: string,
  serverCount: number | undefined,
): number {
  const month = today.slice(0, 7);
  const monthEntries = entries.filter(
    (entry) => !entry.hidden && dateInTimezone(entry.occurred_at, timezone).startsWith(month),
  );
  if (serverCount === undefined) return monthEntries.length;
  return serverCount + monthEntries.filter((entry) => entry.sync_state !== "synced").length;
}

/** Count the current daily completion without adding an acknowledged entry twice. */
export function dailyCheckInMonthCount(input: {
  entries: readonly DiaryEntryRecord[];
  completionEntry: DiaryEntryRecord | null;
  today: string;
  timezone: string;
  serverCount: number | undefined;
}): number {
  const { entries, completionEntry, today, timezone, serverCount } = input;
  const count = monthEntryCount(entries, today, timezone, serverCount);
  if (!completionEntry || completionEntry.hidden) return count;

  const month = today.slice(0, 7);
  if (!dateInTimezone(completionEntry.occurred_at, timezone).startsWith(month)) return count;

  const alreadyCountedLocally = entries.some(
    (entry) =>
      entry.entry_id === completionEntry.entry_id &&
      !entry.hidden &&
      dateInTimezone(entry.occurred_at, timezone).startsWith(month),
  );
  if (alreadyCountedLocally) return count;

  // A synced completion is already represented by the server aggregate.
  if (serverCount !== undefined && completionEntry.sync_state === "synced") return count;
  return count + 1;
}
