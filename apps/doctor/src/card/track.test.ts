import type { Schemas } from "@compass/api-client";
import { describe, expect, it } from "vitest";
import { assessment } from "./fixtures";
import {
  agoText,
  buildTrack,
  deltaText,
  longDay,
  missedOf,
  pieces,
  type Slot,
  scoreLabelAbove,
  slotY,
  trackTrend,
  windowOf,
} from "./track";

const NOW = new Date("2026-10-19T12:00:00+03:00");
const BANDS = [
  { min: 0, max: 4, label: "Минимальная тревога" },
  { min: 5, max: 9, label: "Лёгкая тревога" },
  { min: 10, max: 14, label: "Умеренная тревога" },
  { min: 15, max: 21, label: "Тяжёлая тревога" },
];

function result(day: string, value: number, label: string) {
  return assessment({
    id: day,
    completed_at: `2026-${day}T09:00:00+03:00`,
    scores: {
      total: {
        value,
        band: {
          code: label,
          label,
          source: {
            id: "s",
            locator: "1",
            title: "s",
            url: "https://example.invalid",
            supplementary: false,
          },
        },
      },
    },
  });
}

const kinds = (slots: Slot[]) => slots.map((s) => `${s.kind}:${s.at.slice(5, 10)}`);

describe("buildTrack: точки и пропуски по плановым датам (спека #13, 10.4; #111)", () => {
  it("точки по дате ответа, пропуски — по плановой дате, по возрастанию", () => {
    const track = buildTrack({
      results: [result("10-19", 9, "Лёгкая тревога"), result("10-05", 14, "Умеренная тревога")],
      missed: ["2026-10-12"],
      scoreId: "total",
      bands: BANDS,
    });
    expect(kinds(track)).toEqual(["point:10-05", "gap:10-12", "point:10-19"]);
    expect(track.map((s) => s.lane)).toEqual([2, 2, 1]);
  });

  it("пропуск в начале стоит на дорожке первого измерения", () => {
    const track = buildTrack({
      results: [result("10-12", 12, "Умеренная тревога")],
      missed: ["2026-10-05"],
      scoreId: "total",
      bands: BANDS,
    });
    expect(track.map((s) => [s.kind, s.lane])).toEqual([
      ["gap", 2],
      ["point", 2],
    ]);
  });

  it("окно — последние 6 плановых измерений, пропуски считаются", () => {
    const results = ["09-07", "09-14", "09-21", "09-28", "10-05", "10-19"].map((d) =>
      result(d, 5, "Лёгкая тревога"),
    );
    const track = buildTrack({ results, missed: ["2026-10-12"], scoreId: "total", bands: BANDS });
    expect(kinds(windowOf(track))).toEqual([
      "point:09-14",
      "point:09-21",
      "point:09-28",
      "point:10-05",
      "gap:10-12",
      "point:10-19",
    ]);
  });

  it("окно всегда включает последнее измерение, даже после долгого перерыва", () => {
    const missed = ["10-26", "11-02", "11-09", "11-16", "11-23", "11-30"].map((d) => `2026-${d}`);
    const track = buildTrack({
      results: [result("10-12", 12, "Умеренная тревога"), result("10-19", 9, "Лёгкая тревога")],
      missed,
      scoreId: "total",
      bands: BANDS,
    });
    expect(kinds(windowOf(track))[0]).toBe("point:10-19");
    expect(windowOf(track)).toHaveLength(7);
  });

  it("пропуски без единого измерения не рисуются", () => {
    expect(
      buildTrack({ results: [], missed: ["2026-10-12"], scoreId: "total", bands: BANDS }),
    ).toEqual([]);
  });
});

describe("pieces: сплошная кривая и пунктир через пропуск", () => {
  const p = (at: string): Slot => ({
    kind: "point",
    at,
    lane: 0,
    value: 0,
    band: "",
    result: assessment(),
  });
  const g = (at: string): Slot => ({ kind: "gap", at, lane: 0 });

  it("соседние точки — сплошной участок, через пропуск — пунктир", () => {
    const { solid, dashed } = pieces([p("1"), p("2"), g("3"), p("4"), p("5")]);
    expect(solid.map((run) => run.map((s) => s.at))).toEqual([
      ["1", "2"],
      ["4", "5"],
    ]);
    expect(dashed.map(([a, b]) => [a.at, b.at])).toEqual([["2", "4"]]);
  });

  it("пропуск в начале и в конце — пунктир к ближайшей точке", () => {
    const { solid, dashed } = pieces([g("1"), p("2"), p("3"), g("4")]);
    expect(solid.map((run) => run.map((s) => s.at))).toEqual([["2", "3"]]);
    expect(dashed.map(([a, b]) => [a.at, b.at])).toEqual([
      ["1", "2"],
      ["3", "4"],
    ]);
  });

  it("одна точка — без кривой", () => {
    expect(pieces([p("1")])).toEqual({ solid: [], dashed: [] });
  });
});

describe("trackTrend: смена градации словами в шапке графика (кадры 09, 09б, 09в)", () => {
  it("считается по всей истории, а не по окну графика", () => {
    const track = buildTrack({
      results: [result("09-07", 12, "Умеренная тревога"), result("10-19", 9, "Лёгкая тревога")],
      missed: ["09-14", "09-21", "09-28", "10-05", "10-12"].map((d) => `2026-${d}`),
      scoreId: "total",
      bands: BANDS,
    });
    expect(trackTrend(track, NOW)).toBe("Умеренная тревога 7 сент → Лёгкая тревога 19 окт");
  });

  it("одно измерение и пропуск после него — без «без изменений»", () => {
    const track = buildTrack({
      results: [result("10-12", 12, "Умеренная тревога")],
      missed: ["2026-10-19"],
      scoreId: "total",
      bands: BANDS,
    });
    expect(trackTrend(track, NOW)).toBe("Первое измерение. 19 окт отметки не было");
  });

  it("одно измерение — «Первое измерение»", () => {
    const track = buildTrack({
      results: [result("10-19", 9, "Лёгкая тревога")],
      missed: [],
      scoreId: "total",
      bands: BANDS,
    });
    expect(trackTrend(track, NOW)).toBe("Первое измерение");
  });

  it("смена градации без пропуска — с датой смены", () => {
    const track = buildTrack({
      results: [
        result("10-05", 14, "Умеренная тревога"),
        result("10-12", 12, "Умеренная тревога"),
        result("10-19", 9, "Лёгкая тревога"),
      ],
      missed: [],
      scoreId: "total",
      bands: BANDS,
    });
    expect(trackTrend(track, NOW)).toBe("Умеренная тревога → Лёгкая тревога с 19 окт");
  });

  it("смена через пропуск — даты обоих измерений", () => {
    const track = buildTrack({
      results: [result("10-05", 14, "Умеренная тревога"), result("10-19", 9, "Лёгкая тревога")],
      missed: ["2026-10-12"],
      scoreId: "total",
      bands: BANDS,
    });
    expect(trackTrend(track, NOW)).toBe("Умеренная тревога 5 окт → Лёгкая тревога 19 окт");
  });

  it("пропуск в конце — дата пропуска словами", () => {
    const track = buildTrack({
      results: [result("10-05", 14, "Умеренная тревога"), result("10-12", 12, "Умеренная тревога")],
      missed: ["2026-10-19"],
      scoreId: "total",
      bands: BANDS,
    });
    expect(trackTrend(track, NOW)).toBe("Умеренная тревога, без изменений. 19 окт отметки не было");
  });
});

describe("подписи поповера и шапки", () => {
  it.each([
    [
      12,
      "Умеренная тревога",
      9,
      "Лёгкая тревога",
      "−3 балла к 12 окт, Умеренная тревога → Лёгкая тревога",
    ],
    [14, "Умеренная тревога", 12, "Умеренная тревога", "−2 балла к 12 окт, градация та же"],
    [
      8,
      "Лёгкая тревога",
      12,
      "Умеренная тревога",
      "+4 балла к 12 окт, Лёгкая тревога → Умеренная тревога",
    ],
    [9, "Лёгкая тревога", 9, "Лёгкая тревога", "0 баллов к 12 окт, градация та же"],
  ])("%i (%s) → %i (%s): «%s»", (from, fromBand, to, toBand, text) => {
    expect(
      deltaText(
        { value: from, band: fromBand, at: "2026-10-12T09:00:00+03:00" },
        { value: to, band: toBand },
      ),
    ).toBe(text);
  });

  it("пропуск между измерениями назван датой (кадр 09б)", () => {
    expect(
      deltaText(
        { value: 14, band: "Умеренная тревога", at: "2026-10-05T09:00:00+03:00" },
        { value: 12, band: "Умеренная тревога" },
        ["2026-10-12T12:00:00"],
      ),
    ).toBe("−2 балла к 5 окт, градация та же. 12 окт отметки не было");
  });

  it.each([
    ["2026-10-19T09:00:00+03:00", "сегодня"],
    ["2026-10-18T09:00:00+03:00", "вчера"],
    ["2026-10-16T09:00:00+03:00", "3 дня назад"],
    ["2026-10-12T09:00:00+03:00", "неделю назад"],
    ["2026-10-05T09:00:00+03:00", "2 недели назад"],
  ])("давность %s → «%s»", (at, text) => {
    expect(agoText(at, NOW)).toBe(text);
  });

  it.each([
    [undefined, 2, true],
    [1, 2, true],
    [2, 1, false],
    [2, 2, true],
  ])("подпись балла: было %s, стало %i → над точкой %s", (before, lane, above) => {
    expect(scoreLabelAbove(before, lane, 3)).toBe(above);
  });

  it("на верхней дорожке подпись под точкой: сверху её срежет край графика", () => {
    expect(scoreLabelAbove(2, 3, 3)).toBe(false);
  });

  it("missedOf: пропущенные плановые даты шкалы из расписания", () => {
    const plans: Schemas["ScalePlanOut"][] = [
      {
        scale_id: "gad-7",
        period_days: 14,
        grace_days: 3,
        planned: [
          { planned_on: "2026-10-05", status: "done", assessment_id: null },
          { planned_on: "2026-10-19", status: "missed", assessment_id: null },
          { planned_on: "2026-11-02", status: "upcoming", assessment_id: null },
        ],
      },
    ];
    expect(missedOf(plans, "gad-7")).toEqual(["2026-10-19"]);
    expect(missedOf(plans, "phq-9")).toEqual([]);
  });
});

describe("longDay: дата в поповере", () => {
  it("число, месяц и день недели", () => {
    expect(longDay("2026-10-12T09:00:00+03:00")).toBe("12 октября, понедельник");
  });
});

describe("slotY: пропуск стоит на пунктире", () => {
  const p = (at: string, lane: number): Slot => ({
    kind: "point",
    at,
    lane,
    value: 0,
    band: "",
    result: assessment(),
  });
  const g = (at: string, lane: number): Slot => ({ kind: "gap", at, lane });
  const x = (at: string) => Number(at);
  const y = (lane: number) => 100 - lane * 10;

  it("между точками — на прямой между ними", () => {
    const slots = [p("0", 2), g("10", 2), p("20", 1)];
    expect(slotY(slots, 1, x, y)).toBe(85);
  });

  it("в начале и в конце — на дорожке соседнего измерения", () => {
    const slots = [g("0", 2), p("10", 2), g("20", 2)];
    expect([slotY(slots, 0, x, y), slotY(slots, 2, x, y)]).toEqual([80, 80]);
  });
});
