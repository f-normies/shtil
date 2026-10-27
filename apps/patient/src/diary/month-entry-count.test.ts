import { describe, expect, it } from "vitest";
import { dailyCheckInMonthCount, monthEntryCount } from "./month-entry-count";
import type { DiaryEntryRecord } from "./types";

function localEntry(overrides: Partial<DiaryEntryRecord> = {}): DiaryEntryRecord {
  return {
    audio: null,
    daily_on: null,
    entry_id: "local-entry",
    hidden: false,
    kind: "text",
    mood_level: null,
    mood_words: [],
    occurred_at: "2026-10-24T19:00:00.000Z",
    operation_id: "operation-local-entry",
    other_medicine_text: null,
    owner_id: "patient-a",
    revision: 1,
    sync_state: "pending",
    text: "Локальная запись",
    ...overrides,
  };
}

describe("monthEntryCount", () => {
  it("counts visible pending records and does not count the same record twice after ACK", () => {
    const pending = localEntry();

    expect(monthEntryCount([pending], "2026-10-24", "Europe/Moscow", 2)).toBe(3);
    expect(
      monthEntryCount([localEntry({ sync_state: "synced" })], "2026-10-24", "Europe/Moscow", 3),
    ).toBe(3);
  });

  it("does not count hidden pending records", () => {
    expect(monthEntryCount([localEntry({ hidden: true })], "2026-10-24", "Europe/Moscow", 2)).toBe(
      2,
    );
  });
});

describe("dailyCheckInMonthCount", () => {
  it("uses the system month at a UTC boundary and does not add an acknowledged completion twice", () => {
    const acknowledged = localEntry({
      occurred_at: "2026-10-31T21:30:00.000Z",
      sync_state: "synced",
    });

    expect(
      dailyCheckInMonthCount({
        entries: [acknowledged],
        completionEntry: acknowledged,
        today: "2026-11-01",
        timezone: "Europe/Moscow",
        serverCount: 1,
      }),
    ).toBe(1);
  });

  it("counts the same pending boundary completion once before acknowledgment", () => {
    const pending = localEntry({ occurred_at: "2026-10-31T21:30:00.000Z" });

    expect(
      dailyCheckInMonthCount({
        entries: [pending],
        completionEntry: pending,
        today: "2026-11-01",
        timezone: "Europe/Moscow",
        serverCount: 0,
      }),
    ).toBe(1);
  });
});
