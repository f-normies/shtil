import { describe, expect, it } from "vitest";
import { initials, shortName } from "./names";

describe("имя врача в меню (компонент «Врач v4/Меню»)", () => {
  it.each([
    ["Соколова Ирина Петровна", "Соколова И. П."],
    ["Соколова Ирина", "Соколова И."],
    ["Соколова", "Соколова"],
    ["  Соколова   Ирина  Петровна ", "Соколова И. П."],
  ])("%s → %s", (full, short) => {
    expect(shortName(full)).toBe(short);
  });

  it.each([
    ["Соколова Ирина Петровна", "ИС"],
    ["Соколова", "С"],
    ["", ""],
  ])("инициалы %s → %s", (full, letters) => {
    expect(initials(full)).toBe(letters);
  });
});
