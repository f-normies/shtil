/**
 * Логика графика настроения (спека #115, раздел 6). Правила — аннотация «График
 * настроения: правила для разработки»; данные — пример Лебедевой оттуда же.
 */
import { describe, expect, test } from "vitest";
import { lebedevaMarks, mark, OBSERVATION_START, QUICK, TODAY } from "./diaryFixtures";
import {
  axisLabels,
  buildSlots,
  chartPoints,
  currentLevel,
  curvePath,
  diaryOpensOn,
  gapSpans,
  levelWord,
  levelY,
  moveActive,
  observationZone,
  type Period,
  parsePeriodSearch,
  periodQuery,
  resolvePeriod,
  showsDots,
  slotX,
  tableRows,
  weekPeriod,
} from "./mood";

const period = (days: number): Period => resolvePeriod({ period: days }, TODAY, OBSERVATION_START);
const slotsFor = (days: number, marks = lebedevaMarks()) => buildSlots(period(days), marks);

describe("period", () => {
  test("defaults to two weeks ending today", () => {
    expect(resolvePeriod({}, TODAY, OBSERVATION_START)).toEqual({
      start: "2026-10-06",
      end: "2026-10-19",
      days: 14,
      preset: 14,
      observedFrom: "2026-10-06",
    });
  });

  test.each([
    [7, "2026-10-13"],
    [30, "2026-09-20"],
    [90, "2026-07-22"],
  ])("preset of %i days starts on %s and ends today", (days, start) => {
    expect(period(days)).toMatchObject({ start, end: TODAY, days, preset: days });
  });

  test("the part of a preset before the observation start is not observed", () => {
    expect(period(30).observedFrom).toBe(OBSERVATION_START);
    expect(period(90).observedFrom).toBe(OBSERVATION_START);
  });

  test("accepts a custom period inside observation, 7 to 90 days", () => {
    const custom = resolvePeriod(
      { from: "2026-09-28", to: "2026-10-11" },
      TODAY,
      OBSERVATION_START,
    );
    expect(custom).toEqual({
      start: "2026-09-28",
      end: "2026-10-11",
      days: 14,
      preset: null,
      observedFrom: "2026-09-28",
    });
    expect(resolvePeriod({ from: "2026-10-13", to: TODAY }, TODAY, "2026-01-01").days).toBe(7);
    expect(resolvePeriod({ from: "2026-07-22", to: TODAY }, TODAY, "2026-01-01").days).toBe(90);
  });

  test.each([
    ["shorter than 7 days", "2026-10-01", "2026-10-06", OBSERVATION_START],
    ["longer than 90 days", "2026-07-21", TODAY, "2026-01-01"],
    ["before the observation start", "2026-09-21", "2026-10-05", OBSERVATION_START],
    ["after today", "2026-10-14", "2026-10-20", OBSERVATION_START],
    ["reversed", "2026-10-11", "2026-09-28", OBSERVATION_START],
  ])("falls back to two weeks when the custom period is %s", (_, from, to, observation) => {
    expect(resolvePeriod({ from, to }, TODAY, observation)).toMatchObject({
      start: "2026-10-06",
      preset: 14,
    });
  });

  test("an unknown preset falls back to two weeks", () => {
    expect(resolvePeriod({ period: 12 }, TODAY, OBSERVATION_START).days).toBe(14);
  });

  test("reads the period from the address", () => {
    expect(parsePeriodSearch({ period: 30 })).toEqual({ period: 30 });
    expect(parsePeriodSearch({ period: "90" })).toEqual({ period: 90 });
    expect(parsePeriodSearch({ period: 12 })).toEqual({});
    expect(parsePeriodSearch({ from: "2026-09-28", to: "2026-10-11" })).toEqual({
      from: "2026-09-28",
      to: "2026-10-11",
    });
    expect(parsePeriodSearch({ from: "2026-09-28" })).toEqual({});
    expect(parsePeriodSearch({ from: "вчера", to: "2026-10-11" })).toEqual({});
  });

  test("asks the API for days: both start and end are included", () => {
    expect(periodQuery(period(14))).toEqual({ start: "2026-10-06", end: "2026-10-19" });
    expect(
      periodQuery(
        resolvePeriod({ from: "2026-09-28", to: "2026-10-11" }, TODAY, OBSERVATION_START),
      ),
    ).toEqual({ start: "2026-09-28", end: "2026-10-11" });
  });

  test("the part of a preset before the observation start is not asked: the API refuses it", () => {
    expect(periodQuery(period(30))).toEqual({ start: OBSERVATION_START, end: TODAY });
  });

  test("fewer than 7 observed days cannot be asked: the API takes 7–90 days", () => {
    const fresh = resolvePeriod({ period: 14 }, TODAY, "2026-10-14");
    expect(periodQuery(fresh)).toBeNull();
    expect(periodQuery(resolvePeriod({ period: 14 }, TODAY, "2026-10-13"))).toEqual({
      start: "2026-10-13",
      end: TODAY,
    });
  });

  test("a fresh patient's diary opens on the 7th day of observation", () => {
    expect(diaryOpensOn("2026-10-14")).toBe("2026-10-20");
  });
});

describe("slots", () => {
  test("up to 31 days there is one point per day; several marks give their average", () => {
    const slots = buildSlots(period(14), [
      mark("2026-10-18T21:05:00+03:00", 2),
      mark("2026-10-18T16:02:00+03:00", 3, QUICK),
      mark("2026-10-19T09:10:00+03:00", 3),
    ]);

    expect(slots).toHaveLength(14);
    expect(slots[12]).toMatchObject({ start: "2026-10-18", end: "2026-10-18", value: 2.5 });
    expect(slots[12]?.marks.map((m) => m.occurred_at)).toEqual([
      "2026-10-18T16:02:00+03:00",
      "2026-10-18T21:05:00+03:00",
    ]);
    expect(slots[0]?.value).toBeNull();
  });

  test("marks outside the period are ignored", () => {
    const slots = buildSlots(period(7), lebedevaMarks());
    expect(slots.flatMap((s) => s.marks)).toHaveLength(7);
  });

  test("from 32 days the point is a weekly average, weeks counted back from the end", () => {
    const slots = slotsFor(90);

    expect(slots).toHaveLength(13);
    expect(slots[0]).toMatchObject({ start: "2026-07-22", end: "2026-07-27", days: 6 });
    expect(slots[1]).toMatchObject({ start: "2026-07-28", end: "2026-08-03", days: 7 });
    expect(slots[12]).toMatchObject({ start: "2026-10-13", end: TODAY, days: 7 });
    expect(slots.slice(9).map((s) => s.value)).toEqual([2, 13 / 6, 2.5, 3]);
    expect(slots.slice(9).map((s) => s.markedDays)).toEqual([7, 6, 6, 7]);
  });

  test("a week without marks is a gap", () => {
    const marks = lebedevaMarks().filter(
      (m) => m.occurred_at < "2026-10-06" || m.occurred_at >= "2026-10-13",
    );
    const slots = slotsFor(90, marks);
    expect(slots[11]?.value).toBeNull();
  });

  test("a week is the average of its days, so one busy day does not outweigh the rest", () => {
    const slots = buildSlots(period(90), [
      mark("2026-10-13T09:00:00+03:00", 1),
      mark("2026-10-13T20:00:00+03:00", 3),
      mark("2026-10-14T09:00:00+03:00", 4),
    ]);
    expect(slots[12]?.value).toBe(3);
  });

  test("slots wholly before the observation start are not observed", () => {
    expect(slotsFor(30).map((s) => s.observed)).toEqual([false, false, ...Array(28).fill(true)]);
    expect(slotsFor(90).filter((s) => !s.observed)).toHaveLength(9);
  });
});

describe("geometry", () => {
  test("centres days and levels on the 608 × 184 field", () => {
    expect(slotX(0, 14)).toBe(122);
    expect(slotX(13, 14)).toBe(590);
    expect(levelY(5)).toBe(8);
    expect(levelY(3)).toBe(72);
    expect(levelY(1)).toBe(136);
    expect(levelY(2.5)).toBe(88);
  });

  test.each([
    [7, true],
    [14, true],
    [21, true],
    [30, false],
    [13, true],
  ])("with %i slots ordinary dots are shown: %s", (count, shown) => {
    expect(showsDots(count)).toBe(shown);
  });

  test("a missed day inside the period is a dashed span between its neighbours", () => {
    const points = chartPoints(slotsFor(14));
    expect(points).toHaveLength(13);
    expect(gapSpans(points)).toEqual([{ x1: 266, x2: 338 }]);
  });

  test("gaps at the start and at the end of the period are not drawn", () => {
    const marks = lebedevaMarks().filter(
      (m) =>
        m.occurred_at >= "2026-10-08" &&
        m.occurred_at < "2026-10-18" &&
        !m.occurred_at.startsWith("2026-10-11"),
    );
    const points = chartPoints(slotsFor(14, marks));
    expect(points[0]?.x).toBe(slotX(2, 14));
    expect(points.at(-1)?.x).toBe(slotX(11, 14));
    expect(gapSpans(points)).toEqual([{ x1: 266, x2: 338 }]);
  });

  test("an empty week inside three months is a gap between the weeks around it", () => {
    const marks = lebedevaMarks().filter(
      (m) => m.occurred_at < "2026-10-06" || m.occurred_at >= "2026-10-13",
    );
    const points = chartPoints(slotsFor(90, marks));
    expect(gapSpans(points)).toEqual([{ x1: slotX(10, 13), x2: slotX(12, 13) }]);
  });

  test("the curve is a monotone cubic through the points; one point has no curve", () => {
    const points = chartPoints(slotsFor(14));
    expect(curvePath(points)).toMatch(/^M122,104C/);
    expect(curvePath(points.slice(0, 1))).toBeNull();
  });

  test("marks on one level give a flat line", () => {
    const flat = buildSlots(
      period(7),
      ["13", "14", "15"].map((d) => mark(`2026-10-${d}T09:00:00+03:00`, 3)),
    );
    const path = curvePath(chartPoints(flat)) ?? "";
    // Путь — пары «x,y»: у ровной линии все y на дорожке «Ровно».
    const ys = (path.match(/-?[\d.]+/g) ?? []).map(Number).filter((_, i) => i % 2 === 1);
    expect(ys.length).toBeGreaterThan(0);
    expect(new Set(ys)).toEqual(new Set([72]));
  });

  test("the zone before the observation start covers whole slots", () => {
    expect(observationZone(period(14), slotsFor(14))).toBeNull();
    const month = observationZone(period(30), slotsFor(30));
    expect(month).toMatchObject({ x1: 104, label: "Наблюдение с 22.09", inside: false });
    expect(month?.x2).toBeCloseTo(104 + 2 * (504 / 30));
    const weekly = observationZone(period(90), slotsFor(90));
    expect(weekly?.x2).toBeCloseTo(104 + 9 * (504 / 13));
    expect(weekly?.inside).toBe(true);
  });
});

describe("axis labels", () => {
  test("7 and 14 days: every day with its weekday, month on the first label", () => {
    const labels = axisLabels(slotsFor(14), TODAY);
    expect(labels.map((l) => l.text)).toEqual([
      "6 окт",
      ...["7", "8", "9", "10", "11", "12", "13", "14", "15", "16", "17", "18", "19"],
    ]);
    expect(labels.map((l) => l.weekday).slice(0, 3)).toEqual(["вт", "ср", "чт"]);
    expect(labels.filter((l) => l.today).map((l) => l.text)).toEqual(["19"]);
  });

  test("the first day of a new month gets the month", () => {
    const week = resolvePeriod({ from: "2026-09-29", to: "2026-10-05" }, TODAY, OBSERVATION_START);
    expect(axisLabels(buildSlots(week, []), TODAY).map((l) => l.text)).toEqual([
      "29 сен",
      "30",
      "1 окт",
      "2",
      "3",
      "4",
      "5",
    ]);
  });

  test("a month: every third day counted back from today, no weekdays", () => {
    const labels = axisLabels(slotsFor(30), TODAY);
    expect(labels.map((l) => l.text)).toEqual([
      ...["22 сен", "25", "28", "1 окт", "4", "7", "10", "13", "16", "19"],
    ]);
    expect(labels.every((l) => l.weekday === null)).toBe(true);
  });

  test("three months: every week, the number of its first day under the point", () => {
    const labels = axisLabels(slotsFor(90), TODAY);
    expect(labels.map((l) => l.text)).toEqual([
      ...["22 июл", "28", "4 авг", "11", "18", "25", "1 сен", "8", "15", "22", "29", "6 окт"],
      "13",
    ]);
    expect(labels.at(-1)?.today).toBe(true);
    expect(labels[0]?.x).toBeCloseTo(slotX(0, 13));
  });

  test("labels that do not fit with an 8 px gap take a twice longer step", () => {
    const labels = axisLabels(slotsFor(14), TODAY, 10);
    expect(labels.map((l) => l.text)).toEqual(["7 окт", "11", "15", "19"]);
  });
});

describe("level words and the hidden table", () => {
  test.each([
    [1, "Очень тяжело"],
    [3, "Ровно"],
    [5, "Хорошо"],
    [2.5, "Между «Тяжело» и «Ровно»"],
    [13 / 6, "Между «Тяжело» и «Ровно»"],
  ])("level %d reads «%s»", (value, word) => {
    expect(levelWord(value)).toBe(word);
  });

  test("the table lists every mark of the period: date, level and kind, oldest first", () => {
    const rows = tableRows(
      buildSlots(period(7), [
        mark("2026-10-18T21:05:00+03:00", 2),
        mark("2026-10-16T09:20:00+03:00", 4),
        mark("2026-10-18T16:02:00+03:00", 2, QUICK),
      ]),
    );
    expect(rows).toEqual([
      {
        entryId: "e-2026-10-16T09:20:00+03:00",
        date: "16 октября, пятница",
        time: "09:20",
        level: "Неплохо",
        kind: "Ежедневная отметка",
      },
      {
        entryId: "e-2026-10-18T16:02:00+03:00",
        date: "18 октября, воскресенье",
        time: "16:02",
        level: "Тяжело",
        kind: "Отметка настроения",
      },
      {
        entryId: "e-2026-10-18T21:05:00+03:00",
        date: "18 октября, воскресенье",
        time: "21:05",
        level: "Тяжело",
        kind: "Ежедневная отметка",
      },
    ]);
  });
});

describe("the level of the last point", () => {
  test("is shown on its track only when it is a level, not an average between two", () => {
    expect(currentLevel(chartPoints(slotsFor(14)))).toBe(3);
    const between = buildSlots(period(14), [
      mark("2026-10-19T09:10:00+03:00", 2),
      mark("2026-10-19T21:10:00+03:00", 3, QUICK),
    ]);
    expect(currentLevel(chartPoints(between))).toBeNull();
    expect(currentLevel([])).toBeNull();
  });
});

describe("keyboard", () => {
  const points = chartPoints(slotsFor(14));

  test.each([
    [4, "ArrowRight", 6],
    [6, "ArrowLeft", 4],
    [13, "ArrowRight", 13],
    [0, "ArrowLeft", 0],
    [6, "Home", 0],
    [6, "End", 13],
    [null, "ArrowLeft", 13],
    [null, "ArrowRight", 13],
  ])("from slot %s key %s moves to slot %i", (current, key, expected) => {
    expect(moveActive(points, current, key)).toBe(expected);
  });

  test("without points there is nothing to move to", () => {
    expect(moveActive([], null, "End")).toBeNull();
  });
});

describe("show a week", () => {
  test("a full week becomes a seven-day custom period", () => {
    const week = slotsFor(90)[11];
    expect(week && weekPeriod(week, TODAY, OBSERVATION_START)).toEqual({
      from: "2026-10-06",
      to: "2026-10-12",
    });
  });

  test("the first week of observation starts at the observation start", () => {
    const slots = buildSlots(
      resolvePeriod({ period: 90 }, TODAY, "2026-09-24"),
      lebedevaMarks().filter((m) => m.occurred_at >= "2026-09-24"),
    );
    const week = slots[9];
    expect(week && weekPeriod(week, TODAY, "2026-09-24")).toEqual({
      from: "2026-09-24",
      to: "2026-09-30",
    });
  });
});
