import { describe, expect, test } from "vitest";
import { formatDate, formatDateTime, formatListTime, formatMomentInline } from "./format";

// Время — местное: так «Сегодня, 09:14» не зависит от часового пояса машины.
const NOW = new Date(2026, 9, 27, 12, 0);
const at = (day: number, hour: number, minute: number, month = 9, year = 2026) =>
  new Date(year, month, day, hour, minute).toISOString();

describe("formatDateTime: момент в подробностях (кадры 03, 05)", () => {
  test.each([
    [at(27, 9, 14), "Сегодня, 09:14"],
    [at(27, 0, 3), "Сегодня, 00:03"],
    [at(26, 21, 30), "Вчера, 21:30"],
    [at(16, 9, 12), "16.10, 09:12"],
    [at(2, 7, 5, 0), "02.01, 07:05"],
    [at(30, 18, 20, 11, 2025), "30.12.2025, 18:20"],
  ])("%s → %s", (iso, text) => {
    expect(formatDateTime(iso, NOW)).toBe(text);
  });
});

describe("formatMomentInline: тот же момент внутри предложения", () => {
  test.each([
    [at(27, 9, 14), "сегодня, 09:14"],
    [at(26, 21, 30), "вчера, 21:30"],
    [at(16, 9, 12), "16.10, 09:12"],
  ])("%s → %s", (iso, text) => {
    expect(formatMomentInline(iso, NOW)).toBe(text);
  });
});

describe("formatListTime: время в строке списка (кадр 02)", () => {
  test.each([
    [at(27, 9, 12), "09:12"],
    [at(26, 21, 30), "Вчера"],
    [at(16, 9, 12), "16.10"],
    [at(30, 18, 20, 11, 2025), "30.12.2025"],
  ])("%s → %s", (iso, text) => {
    expect(formatListTime(iso, NOW)).toBe(text);
  });
});

describe("formatDate: день без времени, год только не текущий", () => {
  test.each([
    [at(27, 9, 14), "27.10"],
    [at(16, 9, 12), "16.10"],
    [at(30, 18, 20, 11, 2025), "30.12.2025"],
    // Дата без времени из API не сдвигается часовым поясом.
    ["2026-09-22", "22.09"],
    ["2025-01-03", "03.01.2025"],
  ])("%s → %s", (iso, text) => {
    expect(formatDate(iso, NOW)).toBe(text);
  });
});
