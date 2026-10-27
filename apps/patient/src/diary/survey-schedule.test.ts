import type { Schemas } from "@compass/api-client";
import { expect, it } from "vitest";
import { resolveDiarySchedule } from "./survey-schedule";

function snapshot(
  today: string,
  items: Schemas["ScheduleOut"]["items"] = [],
): Schemas["ScheduleOut"] & { fetched_at: string } {
  return { today, items, fetched_at: `${today}T12:00:00.000Z` };
}

it("sets not_due only from a successful schedule for the current server day", () => {
  expect(resolveDiarySchedule(null, "2026-10-23", [])).toMatchObject({
    status: "unknown",
    needsRefresh: true,
  });
  expect(resolveDiarySchedule(snapshot("2026-10-22"), "2026-10-23", [])).toMatchObject({
    status: "unknown",
    needsRefresh: true,
  });
  const todaySchedule = snapshot("2026-10-23");
  expect(
    resolveDiarySchedule(todaySchedule, "2026-10-23", [], {
      nowMs: Date.parse(todaySchedule.fetched_at) + 5_000,
    }),
  ).toMatchObject({ status: "not_due", dueScaleIds: [], needsRefresh: false });
});

it("uses server due dates and exposes only definitions already cached on the device", () => {
  const schedule = snapshot("2026-10-23", [
    { scale_id: "due", scale_title: "Положена", due_on: "2026-10-22" },
    { scale_id: "today", scale_title: "Сегодня", due_on: "2026-10-23" },
    { scale_id: "later", scale_title: "Позже", due_on: "2026-10-24" },
  ]);

  expect(
    resolveDiarySchedule(schedule, "2026-10-23", ["due"], {
      nowMs: Date.parse(schedule.fetched_at) + 5_000,
    }),
  ).toEqual({
    status: "pending",
    dueScaleIds: ["due", "today"],
    availableScaleIds: ["due"],
    needsRefresh: false,
  });
});

it("keeps planned scales visible but asks for refresh when the same-day snapshot is stale", () => {
  const fetchedAt = Date.parse("2026-10-23T10:00:00.000Z");
  const schedule = {
    today: "2026-10-23",
    fetched_at: new Date(fetchedAt).toISOString(),
    items: [
      { scale_id: "phq-9", scale_title: "PHQ-9", due_on: "2026-10-23" },
      { scale_id: "gad-7", scale_title: "GAD-7", due_on: "2026-10-23" },
    ],
  };

  expect(
    resolveDiarySchedule(schedule, "2026-10-23", ["phq-9"], {
      nowMs: fetchedAt + 30_001,
    }),
  ).toEqual({
    status: "pending",
    dueScaleIds: ["phq-9", "gad-7"],
    availableScaleIds: ["phq-9"],
    needsRefresh: true,
  });
});

it("does not call an empty schedule not-due after freshness expires or its latest refresh fails", () => {
  const fetchedAt = Date.parse("2026-10-23T10:00:00.000Z");
  const schedule = {
    today: "2026-10-23",
    fetched_at: new Date(fetchedAt).toISOString(),
    items: [],
  };

  expect(
    resolveDiarySchedule(schedule, "2026-10-23", [], { nowMs: fetchedAt + 30_001 }),
  ).toMatchObject({ status: "unknown", dueScaleIds: [], needsRefresh: true });
  expect(
    resolveDiarySchedule(schedule, "2026-10-23", [], {
      nowMs: fetchedAt + 5_000,
      latestRequestFailed: true,
    }),
  ).toMatchObject({ status: "unknown", dueScaleIds: [], needsRefresh: true });
});
