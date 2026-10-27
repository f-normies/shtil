import { describe, expect, test } from "vitest";
import { checkInEntryMode, parseMoodSearch } from "./checkin-access";

describe("checkInEntryMode (P2-21)", () => {
  test("без отметки и черновика — новая отметка", () => {
    expect(checkInEntryMode({ hasDailyEntryToday: false, hasDailyDraft: false })).toBe("new");
  });

  test("прямой переход на /check-in после сохранённой отметки — повторная отметка закрыта", () => {
    expect(checkInEntryMode({ hasDailyEntryToday: true, hasDailyDraft: false })).toBe(
      "already-saved",
    );
  });

  test("незавершённый опрос после сохранённой отметки продолжается по черновику", () => {
    expect(checkInEntryMode({ hasDailyEntryToday: true, hasDailyDraft: true })).toBe("draft");
    expect(checkInEntryMode({ hasDailyEntryToday: false, hasDailyDraft: true })).toBe("draft");
  });
});

describe("parseMoodSearch (P2-21: ?mood=3 без JSON-кавычек)", () => {
  test.each([
    [3, 3],
    ["3", 3],
    ['"3"', undefined],
    ["0", undefined],
    [6, undefined],
    [2.5, undefined],
    [undefined, undefined],
    [null, undefined],
  ])("%j → %j", (value, expected) => {
    expect(parseMoodSearch(value)).toBe(expected);
  });
});
