import { describe, expect, it } from "vitest";
import {
  bandTrend,
  laneOf,
  monotonePath,
  scaleDomain,
  shortBandLabel,
  shortScaleName,
} from "./levels";

const NOW = new Date("2026-10-19T12:00:00Z");
const BANDS = [
  { min: 0, max: 4, label: "Минимальная тревога" },
  { min: 10, max: 14, label: "Умеренная тревога" },
  { min: 5, max: 9, label: "Лёгкая тревога" },
  { min: 15, max: 21, label: "Тяжёлая тревога" },
];

describe("shortScaleName", () => {
  it.each([
    ["Опросник здоровья пациента (PHQ-9)", "PHQ-9"],
    ["Шкала тревоги (GAD-7)", "GAD-7"],
    ["Шкала без кода", "Шкала без кода"],
  ])("%s → %s", (title, short) => {
    expect(shortScaleName(title)).toBe(short);
  });
});

describe("laneOf: дорожка градации, лёгкая внизу (спека #13, 10.4)", () => {
  it.each([
    [0, 0],
    [4, 0],
    [5, 1],
    [12, 2],
    [21, 3],
  ])("балл %i → дорожка %i", (value, lane) => {
    expect(laneOf(value, BANDS)).toBe(lane);
  });

  it("балл вне градаций — ближайшая крайняя дорожка", () => {
    expect(laneOf(30, BANDS)).toBe(3);
  });
});

describe("bandTrend: смена градации словами", () => {
  it("градация сменилась — было, стало и дата смены", () => {
    const text = bandTrend(
      [
        { at: "2026-10-05T09:00:00Z", band: "Умеренная тревога" },
        { at: "2026-10-12T09:00:00Z", band: "Умеренная тревога" },
        { at: "2026-10-19T09:00:00Z", band: "Лёгкая тревога" },
      ],
      NOW,
    );
    expect(text).toBe("Умеренная тревога → Лёгкая тревога с 19.10");
  });

  it.each([
    ["2026-10-05T09:00:00Z", "Лёгкая тревога, без изменений 2 недели"],
    ["2026-10-12T09:00:00Z", "Лёгкая тревога, без изменений 1 неделю"],
    ["2026-10-16T09:00:00Z", "Лёгкая тревога, без изменений 3 дня"],
    ["2026-10-19T08:00:00Z", "Лёгкая тревога, без изменений с 19.10"],
  ])("та же градация с %s → «%s»", (since, text) => {
    expect(
      bandTrend(
        [
          { at: since, band: "Лёгкая тревога" },
          { at: "2026-10-19T09:00:00Z", band: "Лёгкая тревога" },
        ],
        NOW,
      ),
    ).toBe(text);
  });

  it("длительность — от начала последней серии одной градации", () => {
    const text = bandTrend(
      [
        { at: "2026-09-01T09:00:00Z", band: "Умеренная тревога" },
        { at: "2026-10-05T09:00:00Z", band: "Лёгкая тревога" },
        { at: "2026-10-19T09:00:00Z", band: "Лёгкая тревога" },
      ],
      NOW,
    );
    expect(text).toBe("Лёгкая тревога, без изменений 2 недели");
  });

  it("одно измерение — только градация", () => {
    expect(bandTrend([{ at: "2026-10-19T09:00:00Z", band: "Лёгкая тревога" }], NOW)).toBe(
      "Лёгкая тревога",
    );
  });

  it("нет измерений — пусто", () => {
    expect(bandTrend([], NOW)).toBe("");
  });
});

/** Опорные точки кубических кривых пути: [x1, y1, x2, y2, x, y] каждого «C». */
function segments(path: string): number[][] {
  return [...path.matchAll(/C([^C]+)/g)].map((m) =>
    (m[1] ?? "")
      .trim()
      .split(/[\s,]+/)
      .map(Number),
  );
}

describe("monotonePath: кривая не выходит за соседние точки", () => {
  it("одна точка — без кривой", () => {
    expect(monotonePath([{ x: 10, y: 10 }])).toBe("");
  });

  it("две точки — отрезок от первой ко второй", () => {
    const path = monotonePath([
      { x: 0, y: 10 },
      { x: 30, y: 20 },
    ]);
    expect(path.startsWith("M0,10")).toBe(true);
    expect(path.endsWith("30,20")).toBe(true);
  });

  it.each([[[0, 30, 30, 0]], [[30, 10, 0, 0, 20]], [[10, 10, 10]], [[0, 40, 0, 40]]])(
    "ys %j: опорные точки каждого участка между его концами",
    (ys) => {
      const points = ys.map((y, i) => ({ x: i * 20, y }));
      const parts = segments(monotonePath(points));
      expect(parts).toHaveLength(points.length - 1);
      parts.forEach(([, y1 = Number.NaN, , y2 = Number.NaN], i) => {
        const ends = [ys[i] ?? 0, ys[i + 1] ?? 0];
        const lo = Math.min(...ends);
        const hi = Math.max(...ends);
        for (const y of [y1, y2]) {
          expect(y).toBeGreaterThanOrEqual(lo - 1e-9);
          expect(y).toBeLessThanOrEqual(hi + 1e-9);
        }
      });
    },
  );

  it("все точки на одной высоте — ровная линия", () => {
    const parts = segments(
      monotonePath([
        { x: 0, y: 10 },
        { x: 20, y: 10 },
        { x: 40, y: 10 },
      ]),
    );
    expect(parts.flatMap(([, y1, , y2, , y]) => [y1, y2, y])).toEqual([10, 10, 10, 10, 10, 10]);
  });
});

// D3-9: подписи дорожек короткие, как в кадре 09; полное название — в подсказке.
describe("shortBandLabel: короткая подпись дорожки по коду градации", () => {
  it.each([
    ["minimal", "Минимальная депрессия или её нет", "Минимальная"],
    ["mild", "Лёгкая депрессия", "Лёгкая"],
    ["moderate", "Умеренная тревога", "Умеренная"],
    ["moderately-severe", "Умеренно тяжёлая депрессия", "Умеренно тяжёлая"],
    ["severe", "Тяжёлая тревога", "Тяжёлая"],
    ["unknown-code", "Своя градация", "Своя градация"],
    [undefined, "Лёгкая депрессия", "Лёгкая депрессия"],
  ])("%s → %s", (code, label, short) => {
    expect(shortBandLabel({ code, label })).toBe(short);
  });
});

describe("scaleDomain: область шкалы в подзаголовке (кадр 09)", () => {
  it.each([
    ["phq-9", "Депрессия"],
    ["gad-7", "Тревога"],
    ["hamd-17", "Депрессия"],
    ["hars", "Тревога"],
    ["other", undefined],
  ])("%s → %s", (id, domain) => {
    expect(scaleDomain(id)).toBe(domain);
  });
});
