import type { Schemas } from "@compass/api-client";
import { describe, expect, test } from "vitest";
import {
  closingMarks,
  doseLine,
  intakeLine,
  intakeSlotLabels,
  isEvening,
  nextStatus,
  slotWhen,
  withMarks,
} from "./doses";

type Dose = Schemas["DoseOut"];

function dose(
  slot: number,
  label: string,
  status: Dose["status"],
  marked_at: string | null = null,
  due = true,
): Dose {
  return {
    prescription_id: "rx-1",
    trade_name: "Авиандр",
    dose_mg: 20,
    slot,
    slot_label: label,
    status,
    marked_at,
    due,
  };
}

describe("подпись приёма (кадр «Пациент · 16»)", () => {
  test.each([
    ["Утро", "Утром"],
    ["День", "Днём"],
    ["Вечер", "Вечером"],
    ["На ночь", "На ночь"],
    ["Приём 2", "Приём 2"],
  ])("%s → %s", (label, when) => {
    expect(slotWhen(label)).toBe(when);
  });

  test("принятая доза — со временем отметки по Москве", () => {
    expect(doseLine(dose(0, "Утро", "taken", "2026-10-18T05:40:00Z"))).toBe(
      "Утром, принято в 08:40",
    );
    expect(doseLine(dose(1, "Вечер", null))).toBe("Вечером");
  });

  test("пропущенная доза отличается от неотмеченной (ревью PR #158)", () => {
    expect(doseLine(dose(1, "Вечер", "missed", "2026-10-18T18:00:00Z"))).toBe("Вечером, пропущено");
  });
});

describe("отметка", () => {
  test("касание пустого — принято, отмеченного — снять", () => {
    expect(nextStatus(null)).toBe("taken");
    expect(nextStatus("missed")).toBe("taken");
    expect(nextStatus("taken")).toBeNull();
  });

  test("вечер — с 17:00 по Москве", () => {
    expect(isEvening(new Date("2026-10-18T13:59:00Z"))).toBe(false);
    expect(isEvening(new Date("2026-10-18T14:00:00Z"))).toBe(true);
  });

  test("«Готово» сохраняет пустые приёмы как пропущенные", () => {
    const marks = closingMarks("2026-10-18", [
      dose(0, "Утро", "taken", "2026-10-18T05:40:00Z"),
      dose(1, "Вечер", null),
    ]);
    expect(marks).toEqual([
      { prescription_id: "rx-1", day: "2026-10-18", slot: 1, status: "missed" },
    ]);
  });

  test("приём, время которого не пришло, «Готово» не трогает (ревью PR #158)", () => {
    const marks = closingMarks("2026-10-18", [
      dose(0, "Утро", null),
      dose(1, "На ночь", null, null, false),
    ]);
    expect(marks.map((m) => m.slot)).toEqual([0]);
  });
});

describe("отметка видна сразу, до ответа сервера", () => {
  test("отметки применяются к дозам дня", () => {
    const now = new Date("2026-10-18T05:40:00Z");
    const day = { day: "2026-10-18", doses: [dose(0, "Утро", null), dose(1, "Вечер", "taken")] };

    const next = withMarks(
      day,
      [
        { prescription_id: "rx-1", day: "2026-10-18", slot: 0, status: "taken" },
        { prescription_id: "rx-1", day: "2026-10-18", slot: 1, status: null },
      ],
      now,
    );

    expect(next.doses.map((d) => [d.status, d.marked_at])).toEqual([
      ["taken", "2026-10-18T05:40:00.000Z"],
      [null, null],
    ]);
    expect(day.doses[0]?.status).toBeNull();
  });
});

describe("строка приёма в отметке и на главной (P2-05, кадры 12 и 16)", () => {
  const prescription = {
    prescription_id: "rx-1",
    trade_name: "Авиандр",
    dose_mg: 20,
    times_per_day: 2,
    schedule: "утром и вечером",
  };

  test.each([
    [2, "утром и вечером", ["Утром", "Вечером"]],
    [1, "утром", ["Утром"]],
    [3, "утром, днём и вечером", ["Утром", "Днём", "Вечером"]],
    [1, "на ночь", ["На ночь"]],
    [1, "внутрь после еды", ["Приём"]],
    [2, "внутрь после еды", ["Приём 1", "Приём 2"]],
    [3, "утром и вечером", ["Приём 1", "Приём 2", "Приём 3"]],
  ])("%i × «%s» → %j", (timesPerDay, schedule, labels) => {
    expect(intakeSlotLabels(timesPerDay, schedule)).toEqual(labels);
  });

  test("принятая доза — «Утром, принято в 08:40», иначе только время приёма", () => {
    expect(
      intakeLine(prescription, 1, { status: "taken", occurred_at: "2026-10-18T05:40:00Z" }),
    ).toBe("Утром, принято в 08:40");
    expect(intakeLine(prescription, 2, undefined)).toBe("Вечером");
    expect(intakeLine(prescription, 2, { status: "unmarked", occurred_at: null })).toBe(
      "Вечером, приём не отмечен",
    );
  });
});
