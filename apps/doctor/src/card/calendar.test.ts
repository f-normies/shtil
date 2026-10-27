/**
 * Поповер календаря (кадр 06г, компоненты «Врач v4/Поповер календаря» и «…/День»):
 * сетка месяца, доступные дни, выбор начала и конца, клавиатура.
 */
import { describe, expect, test } from "vitest";
import {
  addMonths,
  canPage,
  dayState,
  focusInView,
  monthOfDay,
  monthTitle,
  monthWeeks,
  moveDay,
  pickDay,
  selectionText,
} from "./calendar";
import { OBSERVATION_START, TODAY } from "./diaryFixtures";

const limits = { today: TODAY, observationStart: OBSERVATION_START };
const none = { start: null, end: null };

describe("month grid", () => {
  test("weeks start on Monday, empty cells before the first day", () => {
    const weeks = monthWeeks("2026-09");
    expect(weeks).toHaveLength(5);
    expect(weeks[0]).toEqual([
      null,
      ...["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-05", "2026-09-06"],
    ]);
    expect(weeks[4]).toEqual(["2026-09-28", "2026-09-29", "2026-09-30", null, null, null, null]);
  });

  test("months by name and by step", () => {
    expect(monthTitle("2026-09")).toBe("Сентябрь 2026");
    expect(monthOfDay(TODAY)).toBe("2026-10");
    expect(addMonths("2026-01", -1)).toBe("2025-12");
    expect(addMonths("2026-12", 1)).toBe("2027-01");
  });

  test("arrows stop at the months of the observation start and of today", () => {
    expect(canPage("2026-09", -1, limits)).toBe(false);
    expect(canPage("2026-10", 1, limits)).toBe(false);
    expect(canPage("2026-10", -1, limits)).toBe(true);
    expect(canPage("2026-09", 1, limits)).toBe(true);
  });
});

describe("available days", () => {
  test("days before the observation start and after today are unavailable", () => {
    expect(dayState("2026-09-21", none, limits).disabled).toBe(true);
    expect(dayState("2026-09-22", none, limits).disabled).toBe(false);
    expect(dayState(TODAY, none, limits)).toMatchObject({ disabled: false, today: true });
    expect(dayState("2026-10-20", none, limits).disabled).toBe(true);
  });

  test("after the start, days closer than 7 days are muted; earlier days move the start", () => {
    const started = { start: "2026-10-01", end: null };
    expect(dayState("2026-10-01", started, limits)).toMatchObject({ start: true, disabled: false });
    expect(dayState("2026-10-02", started, limits).disabled).toBe(true);
    expect(dayState("2026-10-06", started, limits).disabled).toBe(true);
    expect(dayState("2026-10-07", started, limits).disabled).toBe(false);
    expect(dayState("2026-09-28", started, limits).disabled).toBe(false);
  });

  test("days farther than 90 days from the start are unavailable", () => {
    const wide = { today: TODAY, observationStart: "2026-01-01" };
    const started = { start: "2026-07-01", end: null };
    expect(dayState("2026-09-28", started, wide).disabled).toBe(false);
    expect(dayState("2026-09-29", started, wide).disabled).toBe(true);
  });

  test("a chosen period marks its ends and the days between", () => {
    const chosen = { start: "2026-09-28", end: "2026-10-11" };
    expect(dayState("2026-09-28", chosen, limits)).toMatchObject({ start: true, inRange: false });
    expect(dayState("2026-10-05", chosen, limits)).toMatchObject({ inRange: true });
    expect(dayState("2026-10-11", chosen, limits)).toMatchObject({ end: true });
    expect(dayState("2026-10-12", chosen, limits)).toMatchObject({ inRange: false });
  });
});

describe("picking days", () => {
  test("the first click is the start, the second is the end", () => {
    const started = pickDay(none, "2026-09-28", limits);
    expect(started).toEqual({ start: "2026-09-28", end: null });
    expect(pickDay(started, "2026-10-11", limits)).toEqual({
      start: "2026-09-28",
      end: "2026-10-11",
    });
  });

  test("a click before the start moves the start", () => {
    expect(pickDay({ start: "2026-10-01", end: null }, "2026-09-25", limits)).toEqual({
      start: "2026-09-25",
      end: null,
    });
  });

  test("a click after a chosen period starts a new one", () => {
    const chosen = { start: "2026-09-28", end: "2026-10-11" };
    expect(pickDay(chosen, "2026-10-01", limits)).toEqual({ start: "2026-10-01", end: null });
  });

  test("unavailable days are not picked", () => {
    const started = { start: "2026-10-01", end: null };
    expect(pickDay(started, "2026-10-04", limits)).toBe(started);
    expect(pickDay(none, "2026-09-01", limits)).toBe(none);
  });

  test.each([
    [none, "Выберите начало периода"],
    [{ start: "2026-10-01", end: null }, "Начало — 1 октября, выберите конец"],
    [{ start: "2026-09-28", end: "2026-10-11" }, "28 сентября — 11 октября, 14 дней"],
  ])("footer for %j: %s", (selection, text) => {
    expect(selectionText(selection)).toBe(text);
  });
});

describe("keyboard", () => {
  test.each([
    ["2026-10-08", "ArrowLeft", "2026-10-07"],
    ["2026-10-08", "ArrowRight", "2026-10-09"],
    ["2026-10-08", "ArrowUp", "2026-10-01"],
    ["2026-10-08", "ArrowDown", "2026-10-15"],
    ["2026-10-08", "PageUp", "2026-09-22"],
    ["2026-10-15", "ArrowDown", TODAY],
    ["2026-09-30", "PageDown", TODAY],
    ["2026-09-30", "Enter", "2026-09-30"],
  ])("from %s key %s moves to %s, inside observation", (day, key, expected) => {
    expect(moveDay(day, key, limits)).toBe(expected);
  });
});

describe("focus after paging months", () => {
  const limits = { today: TODAY, observationStart: "2026-06-03" };

  test("a focused day in view stays", () => {
    expect(focusInView("2026-10-19", "2026-10", limits)).toBe("2026-10-19");
    expect(focusInView("2026-09-14", "2026-10", limits)).toBe("2026-09-14");
  });

  test("paging back moves the focused day into the later visible month, same date", () => {
    expect(focusInView("2026-10-19", "2026-09", limits)).toBe("2026-09-19");
    expect(focusInView("2026-10-31", "2026-09", limits)).toBe("2026-09-30");
  });

  test("paging forward moves it into the earlier visible month, within the limits", () => {
    expect(focusInView("2026-06-10", "2026-08", limits)).toBe("2026-07-10");
    expect(focusInView("2026-08-25", "2026-10", limits)).toBe("2026-09-25");
    expect(
      focusInView("2026-06-02", "2026-06", { ...limits, observationStart: "2026-06-03" }),
    ).toBe("2026-06-03");
  });
});
