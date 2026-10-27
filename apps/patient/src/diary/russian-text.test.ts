import { describe, expect, test } from "vitest";
import {
  capitalizeFirst,
  entriesCountLabel,
  entriesNoun,
  greetingName,
  monthInPrepositional,
} from "./russian-text";

describe("entriesCountLabel (P2-04)", () => {
  test.each([
    [0, "0 записей"],
    [1, "1 запись"],
    [2, "2 записи"],
    [4, "4 записи"],
    [5, "5 записей"],
    [7, "7 записей"],
    [11, "11 записей"],
    [12, "12 записей"],
    [13, "13 записей"],
    [21, "21 запись"],
    [22, "22 записи"],
    [111, "111 записей"],
  ])("%i → %s", (count, label) => {
    expect(entriesCountLabel(count)).toBe(label);
  });
});

describe("entriesNoun", () => {
  test("слово отдельно от числа", () => {
    expect(entriesNoun(1)).toBe("запись");
    expect(entriesNoun(3)).toBe("записи");
    expect(entriesNoun(14)).toBe("записей");
  });
});

describe("monthInPrepositional (P2-04)", () => {
  test.each([
    ["2026-01-15", "в январе"],
    ["2026-02-01", "в феврале"],
    ["2026-03-31", "в марте"],
    ["2026-08-09", "в августе"],
    ["2026-10-27", "в октябре"],
    ["2026-12-31", "в декабре"],
  ])("%s → %s", (date, label) => {
    expect(monthInPrepositional(date)).toBe(label);
  });
});

describe("capitalizeFirst (P2-11)", () => {
  test("заглавная только у первой буквы, месяц остаётся строчным", () => {
    expect(capitalizeFirst("вторник, 27 октября")).toBe("Вторник, 27 октября");
    expect(capitalizeFirst("")).toBe("");
  });
});

describe("greetingName (P2-11 / M-8)", () => {
  test.each([
    ["Марина", "Лебедева Марина Викторовна", "Марина"],
    ["", "Лебедева Марина Викторовна", "Марина"],
    ["   ", "Лебедева Марина Викторовна", "Марина"],
    [undefined, "Лебедева Марина Викторовна", "Марина"],
    ["Лебедева Марина Викторовна", "Лебедева Марина Викторовна", "Марина"],
    ["Лебедева Марина Викторовна ", "Лебедева Марина Викторовна", "Марина"],
    [undefined, "Марина", "Марина"],
    [undefined, "", ""],
    [undefined, undefined, ""],
  ])("display_name=%j, full_name=%j → %j", (displayName, fullName, expected) => {
    expect(greetingName(displayName, fullName)).toBe(expected);
  });
});
