import { describe, expect, test } from "vitest";
import {
  addDays,
  dayMonth,
  dayMonthShort,
  dayNumber,
  dayOf,
  daysBetween,
  isDay,
  plural,
  rangeText,
  SERVER_TIME_ZONE,
  shortDate,
  timeOf,
  weekdayLong,
  weekdayShort,
} from "./days";

// Дни и время считаются в поясе сервера (Москва), как в API #31. Пояс машины не влияет:
// тесты гоняются и с `TZ=Asia/Vladivostok`, и с `TZ=America/New_York`.

describe("dayOf", () => {
  test("takes the server calendar date of a moment", () => {
    expect(SERVER_TIME_ZONE).toBe("Europe/Moscow");
    expect(dayOf("2026-10-19T00:05:00+03:00")).toBe("2026-10-19");
    expect(dayOf("2026-10-19T23:55:00+03:00")).toBe("2026-10-19");
    expect(dayOf(new Date("2026-01-03T12:00:00+03:00"))).toBe("2026-01-03");
  });

  test("a moment after midnight in Moscow is the next day there, wherever the doctor is", () => {
    expect(dayOf("2026-10-18T21:30:00Z")).toBe("2026-10-19");
    expect(dayOf("2026-10-19T20:59:00Z")).toBe("2026-10-19");
    expect(dayOf("2026-10-19T08:00:00+10:00")).toBe("2026-10-19");
    expect(dayOf("2026-10-20T06:30:00+10:00")).toBe("2026-10-19");
  });
});

describe("day arithmetic", () => {
  test.each([
    ["2026-10-19", -13, "2026-10-06"],
    ["2026-10-19", -89, "2026-07-22"],
    ["2026-09-30", 1, "2026-10-01"],
    ["2026-12-31", 1, "2027-01-01"],
    ["2026-03-28", 2, "2026-03-30"],
  ])("%s + %i days is %s", (day, n, expected) => {
    expect(addDays(day, n)).toBe(expected);
  });

  test("counts days between two dates", () => {
    expect(daysBetween("2026-10-06", "2026-10-19")).toBe(13);
    expect(daysBetween("2026-10-19", "2026-10-06")).toBe(-13);
    expect(daysBetween("2026-03-28", "2026-03-30")).toBe(2);
  });

  test.each([
    ["2026-10-19", true],
    ["2026-02-30", false],
    ["2026-10-1", false],
    ["вчера", false],
  ])("isDay(%s) is %s", (value, expected) => {
    expect(isDay(value)).toBe(expected);
  });
});

describe("Russian date words", () => {
  test("formats days the way the frames do", () => {
    expect(dayMonth("2026-10-19")).toBe("19 октября");
    expect(dayMonthShort("2026-09-22")).toBe("22 сен");
    expect(dayMonthShort("2026-07-22")).toBe("22 июл");
    expect(dayNumber("2026-10-07")).toBe("7");
    expect(shortDate("2026-09-22")).toBe("22.09");
    expect(weekdayShort("2026-10-19")).toBe("пн");
    expect(weekdayLong("2026-10-16")).toBe("пятница");
    expect(timeOf("2026-10-19T09:05:00+03:00")).toBe("09:05");
    expect(timeOf("2026-10-18T21:30:00Z")).toBe("00:30");
  });

  test.each([
    ["2026-10-06", "2026-10-19", "6–19 октября"],
    ["2026-09-20", "2026-10-19", "20 сентября — 19 октября"],
    ["2025-12-29", "2026-01-04", "29 декабря 2025 — 4 января 2026"],
  ])("range %s … %s reads «%s»", (start, end, expected) => {
    expect(rangeText(start, end)).toBe(expected);
  });
});

describe("plural", () => {
  test.each([
    [1, "день"],
    [2, "дня"],
    [5, "дней"],
    [11, "дней"],
    [14, "дней"],
    [21, "день"],
    [22, "дня"],
    [28, "дней"],
  ])("%i %s", (n, word) => {
    expect(plural(n, "день", "дня", "дней")).toBe(word);
  });
});
