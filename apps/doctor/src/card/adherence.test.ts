import type { Schemas } from "@compass/api-client";
import { describe, expect, it } from "vitest";
import {
  adherenceFindings,
  cellLabel,
  courseNote,
  dosesText,
  indicatorText,
  lanesOf,
  markedText,
  missedText,
  percentOf,
  periodTitle,
  recordedAt,
  regimenLine,
  shareNote,
  weekdayShort,
  weeksOf,
} from "./adherence";

type Adherence = Schemas["AdherenceOut"];
type Proposal = Schemas["ProposalOut"];

const PID = "p1";

/** Кадр 07: 5–18 октября, Авиандр 2 раза в сутки, пропущены вечера 9.10 и 14.10. */
function frame07(): Adherence {
  const days = Array.from({ length: 14 }, (_, i) => {
    const day = `2026-10-${String(5 + i).padStart(2, "0")}`;
    const missed = day === "2026-10-09" || day === "2026-10-14";
    return {
      day,
      doses: [
        { prescription_id: PID, slot: 0, status: "taken" as const, marked_at: `${day}T05:40:00Z` },
        {
          prescription_id: PID,
          slot: 1,
          status: missed ? ("missed" as const) : ("taken" as const),
          marked_at: `${day}T18:05:00Z`,
        },
      ],
    };
  });
  return {
    period: { start: "2026-10-05", end: "2026-10-18" },
    course: "active",
    prescriptions: [
      {
        id: PID,
        trade_name: "Авиандр",
        dose_mg: 20,
        times_per_day: 2,
        schedule: "утром и вечером",
        slot_labels: ["Утро", "Вечер"],
        started_on: "2026-10-05",
        ends_on: null,
        stopped_on: null,
      },
    ],
    days,
    planned: 28,
    taken: 26,
    missed: 2,
    unmarked: 0,
    marked_any: true,
  };
}

describe("доля приёма (issue #34, кадр 07)", () => {
  it("26 из 28 → 93%", () => {
    expect(percentOf(frame07())).toBe(93);
    expect(dosesText(26, 28)).toBe("26 из 28 доз");
  });

  it.each([
    [1, 1, "1 из 1 дозы"],
    [20, 21, "20 из 21 дозы"],
    [0, 2, "0 из 2 доз"],
  ])("%i из %i → «%s»", (taken, planned, text) => {
    expect(dosesText(taken, planned)).toBe(text);
  });

  it("без отметок доли нет", () => {
    expect(percentOf({ ...frame07(), marked_any: false, taken: 0, missed: 0 })).toBeNull();
  });

  it("период и источник рядом с числом", () => {
    expect(periodTitle({ start: "2026-10-05", end: "2026-10-18" })).toBe(
      "Приём за 14 дней, 5–18 октября",
    );
    expect(periodTitle({ start: "2026-09-28", end: "2026-10-11" })).toBe(
      "Приём за 14 дней, 28 сентября – 11 октября",
    );
    expect(shareNote("female")).toBe("за 14 дней, по отметкам пациентки");
    expect(shareNote(null)).toBe("за 14 дней, по отметкам пациента");
  });
});

describe("пропуски и схема", () => {
  it("2 вечерние дозы: 9.10 и 14.10", () => {
    expect(missedText(frame07())).toBe("2 вечерние дозы: 9.10 и 14.10");
  });

  it("разные приёмы — с подписью у каждой даты", () => {
    const a = frame07();
    const first = a.days[0]?.doses[0];
    if (first) {
      first.status = "missed";
    }
    expect(missedText({ ...a, missed: 3 })).toBe("3 дозы: 5.10 утро, 9.10 вечер, 14.10 вечер");
  });

  it("без пропусков строки нет", () => {
    expect(missedText({ ...frame07(), days: [], missed: 0 })).toBeNull();
  });

  it("схема по кадру", () => {
    expect(regimenLine(frame07().prescriptions[0] as Adherence["prescriptions"][number])).toBe(
      "Авиандр 20 мг, 2 раза в сутки, утром и вечером, с 05.10",
    );
  });

  it.each([
    ["active", null],
    ["completed", "курс завершён"],
    ["stopped", "назначение отменено 10.10"],
  ] as const)("%s → %s", (course, note) => {
    const a = frame07();
    const p = a.prescriptions[0];
    if (p && course === "stopped") {
      p.stopped_on = "2026-10-10";
    }
    expect(courseNote({ ...a, course })).toBe(note);
  });

  it("строка «Приём» в «Состоянии» говорит, что курс кончился (ревью PR #158)", () => {
    const a = frame07();
    expect(indicatorText(a)).toBe("26 из 28 доз за 14 дней");
    expect(indicatorText({ ...a, course: "completed" })).toBe(
      "26 из 28 доз за 14 дней, курс завершён",
    );
    expect(indicatorText({ ...a, marked_any: false })).toBe("нет отметок");
  });
});

describe("календарь", () => {
  it("недели с понедельника", () => {
    const weeks = weeksOf(frame07().days);
    expect(weeks.map((w) => w.length)).toEqual([7, 7]);
    expect(weeksOf(frame07().days.slice(2)).map((w) => w.length)).toEqual([5, 7]);
  });

  it("дорожки — приёмы назначений", () => {
    expect(lanesOf(frame07()).map((l) => l.label)).toEqual(["Утро", "Вечер"]);
  });

  it.each([
    ["missed", "14 октября, вечер, пропущена, по отметке пациентки"],
    ["taken", "14 октября, вечер, принята, по отметке пациентки"],
    [null, "14 октября, вечер, нет отметки"],
  ] as const)("метка ячейки для читалки: %s", (status, label) => {
    expect(cellLabel("2026-10-14", "Вечер", status, "female")).toBe(label);
  });
});

function finding(
  status: string,
  created_at: string,
  category = "adherence",
  recorded_at?: string,
): Proposal {
  return {
    id: `${status}-${created_at}-${category}`,
    patient_id: "x",
    kind: "freetext.finding",
    status,
    payload: { category, ...(recorded_at ? { recorded_at } : {}) },
    created_at,
  } as unknown as Proposal;
}

describe("записи пациента о приёме", () => {
  it("находки adherence внутри периода: ждущие выше подтверждённых, новые выше", () => {
    const shown = adherenceFindings(
      [
        finding("proposed", "2026-10-14T19:10:00Z"),
        finding("confirmed", "2026-10-15T05:40:00Z"),
        finding("rejected", "2026-10-15T06:00:00Z"),
        finding("proposed", "2026-10-15T06:00:00Z", "symptom"),
        finding("proposed", "2026-10-01T06:00:00Z"),
      ],
      { start: "2026-10-05", end: "2026-10-18" },
    );
    expect(shown.map((p) => p.status)).toEqual(["proposed", "confirmed"]);
  });

  it("дата записи — `recorded_at`, как в очереди #96; без неё — время предложения", () => {
    // Ревью PR #158: голосовую запись разобрали позже, чем пациент её сделал.
    const late = finding("proposed", "2026-10-19T06:00:00Z", "adherence", "2026-10-18T19:00:00Z");
    const plain = finding("confirmed", "2026-10-15T05:40:00Z");

    expect(recordedAt(late)).toBe("2026-10-18T19:00:00Z");
    expect(recordedAt(plain)).toBe("2026-10-15T05:40:00Z");
    expect(adherenceFindings([late], { start: "2026-10-05", end: "2026-10-18" })).toEqual([late]);
  });
});

describe("поповер и шапка календаря", () => {
  it.each([
    ["2026-10-14", "2026-10-14T19:10:00Z", "отмечено в 22:10"],
    // Отметка пришла позже: точка в день дозы, время отметки с датой.
    ["2026-10-14", "2026-10-16T06:00:00Z", "отмечено 16.10 в 09:00"],
  ])("%s, %s → «%s»", (day, at, text) => {
    expect(markedText(day, at)).toBe(text);
  });

  it("день недели коротко", () => {
    expect(weekdayShort("2026-10-05")).toBe("пн");
    expect(weekdayShort("2026-10-18")).toBe("вс");
  });
});
