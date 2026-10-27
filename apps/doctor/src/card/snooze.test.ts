import { describe, expect, test } from "vitest";
import { placePopover, snoozeDateError } from "./snooze";

describe("snoozeDateError: «Другая дата…» с завтра до предела (кадр 11б)", () => {
  // Сегодня по Москве — 27 октября, предел — 14 дней (SnoozeOptionsOut.max_days).
  const today = "2026-10-27";

  test.each([
    ["2026-10-20", "Дата должна быть не раньше завтра, 28 октября"],
    ["2026-10-27", "Дата должна быть не раньше завтра, 28 октября"],
    ["2026-10-28", null],
    ["2026-11-10", null],
    ["2026-11-11", "Отложить можно не дальше 10 ноября"],
  ])("%s", (date, error) => {
    expect(snoozeDateError(date, today, 14)).toBe(error);
  });

  test("пустое поле — ошибки нет, кнопка и так неактивна", () => {
    expect(snoozeDateError("", today, 14)).toBeNull();
  });
});

describe("placePopover: под кнопкой, если помещается, иначе над ней (кадр 11б)", () => {
  const panel = { width: 320, height: 300 };
  const viewport = { width: 1280, height: 800 };

  test("под кнопкой, по центру кнопки", () => {
    const anchor = { top: 390, bottom: 430, left: 700, right: 840 };
    expect(placePopover(anchor, panel, viewport)).toEqual({ side: "below", top: 438, left: 610 });
  });

  test("внизу мало места — над кнопкой", () => {
    const anchor = { top: 700, bottom: 740, left: 700, right: 840 };
    expect(placePopover(anchor, panel, viewport)).toEqual({ side: "above", top: 392, left: 610 });
  });

  test("не помещается ни снизу, ни сверху — там, где места больше, и не выше края", () => {
    const anchor = { top: 100, bottom: 140, left: 700, right: 840 };
    const tall = { width: 320, height: 700 };
    expect(placePopover(anchor, tall, viewport)).toEqual({ side: "below", top: 148, left: 610 });
    const low = { top: 700, bottom: 740, left: 700, right: 840 };
    expect(placePopover(low, tall, viewport)).toEqual({ side: "above", top: 8, left: 610 });
  });

  test("не выходит за края экрана по горизонтали", () => {
    const left = { top: 590, bottom: 630, left: 10, right: 150 };
    expect(placePopover(left, panel, viewport).left).toBe(8);
    const right = { top: 590, bottom: 630, left: 1200, right: 1270 };
    expect(placePopover(right, panel, viewport).left).toBe(1280 - 320 - 8);
  });
});
