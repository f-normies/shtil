import { expect, it } from "vitest";
import { diaryDateInTimezone, diaryWeekDays, diaryWeekRange, resolveDiaryClock } from "./clock";
import type { DiaryContextCacheRecord } from "./types";

it("anchors offline time to the last verified server time and crosses server midnight", () => {
  const cache: DiaryContextCacheRecord = {
    owner_id: "owner-a",
    context: {
      daily_status: "missing",
      month_entry_count: 0,
      observation_from: "2026-01-01",
      server_now: "2026-10-23T20:55:00.000Z",
      timezone: "Europe/Moscow",
      today: "2026-10-23",
    },
    context_fetched_at: "2026-10-23T20:55:00.000Z",
    schedule_snapshot: null,
  };

  expect(resolveDiaryClock(cache, Date.parse("2026-10-23T21:10:00.000Z"))).toEqual({
    occurredAt: "2026-10-23T21:10:00.000Z",
    today: "2026-10-24",
    timezone: "Europe/Moscow",
    source: "cached-context",
  });
});

it("uses the configured fallback zone and device time when no context has ever been cached", () => {
  expect(resolveDiaryClock(null, Date.parse("2026-10-23T21:10:00.000Z"))).toEqual({
    occurredAt: "2026-10-23T21:10:00.000Z",
    today: "2026-10-24",
    timezone: "Europe/Moscow",
    source: "device",
  });
});

it("groups a month-boundary entry by the patient's local calendar day", () => {
  expect(diaryDateInTimezone("2026-12-31T21:30:00.000Z", "Europe/Moscow")).toBe("2027-01-01");
});

it("shows two days before today and four days after it", () => {
  expect(diaryWeekDays("2026-10-19")).toEqual([
    "2026-10-17",
    "2026-10-18",
    "2026-10-19",
    "2026-10-20",
    "2026-10-21",
    "2026-10-22",
    "2026-10-23",
  ]);
  expect(diaryWeekRange("2026-10-19")).toBe("17–23 октября");
});
