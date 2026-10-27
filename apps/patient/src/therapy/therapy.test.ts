import { describe, expect, test } from "vitest";
import {
  errorStatus,
  lastDayIso,
  moscowDayIso,
  regimenText,
  reportErrorText,
  toggleReaction,
} from "./therapy";

describe("regimenText", () => {
  test.each([
    [1, "по 20 мг 1 раз в сутки, утром"],
    [2, "по 20 мг 2 раза в сутки, утром"],
    [5, "по 20 мг 5 раз в сутки, утром"],
    [12, "по 20 мг 12 раз в сутки, утром"],
  ])("%i times a day", (times, expected) => {
    expect(regimenText({ dose_mg: 20, times_per_day: times, schedule: "утром" })).toBe(expected);
  });
});

test("last day of the course is the day before ends_on", () => {
  expect(lastDayIso("2026-11-01")).toBe("2026-10-31");
  expect(lastDayIso("2026-03-01")).toBe("2026-02-28");
});

test("toggling a reaction keeps the order of the list", () => {
  const order = ["a", "b", "c"];
  expect(toggleReaction([], "c", order)).toEqual(["c"]);
  expect(toggleReaction(["c"], "a", order)).toEqual(["a", "c"]);
  expect(toggleReaction(["a", "c"], "a", order)).toEqual(["c"]);
});

describe("reportErrorText", () => {
  test.each([
    [409, "Назначение уже не действует. Обновите страницу."],
    [422, "Не удалось принять отметку. Обновите страницу и выберите реакции из списка."],
    [500, "Не удалось отправить. Проверьте связь и попробуйте ещё раз."],
    [undefined, "Не удалось отправить. Проверьте связь и попробуйте ещё раз."],
  ])("status %s", (status, expected) => {
    expect(reportErrorText(status)).toBe(expected);
  });
});

test("error status comes from a problem+json body", () => {
  expect(errorStatus({ status: 409, detail: "x" })).toBe(409);
  expect(errorStatus(new TypeError("Failed to fetch"))).toBeUndefined();
  expect(errorStatus(null)).toBeUndefined();
});

describe("moscowDayIso", () => {
  test("подтверждение после полуночи по Москве — уже следующий день", () => {
    expect(moscowDayIso("2026-10-19T22:30:00Z")).toBe("2026-10-20");
  });

  test("днём дата по Москве совпадает с датой UTC", () => {
    expect(moscowDayIso("2026-10-20T09:00:00Z")).toBe("2026-10-20");
  });
});
