/**
 * Тексты графика настроения: итог над графиком, строка под ним и поповер точки
 * (кадры 06–06д и доска состояний графика). Данные — пример Лебедевой из аннотации.
 */
import { describe, expect, test } from "vitest";
import { lebedevaMarks, mark, OBSERVATION_START, QUICK, TODAY } from "./diaryFixtures";
import { buildSlots, type Period, resolvePeriod } from "./mood";
import { coverage, headline, pointDetails, summaryLine } from "./moodSummary";

const period = (days: number, observation = OBSERVATION_START): Period =>
  resolvePeriod({ period: days }, TODAY, observation);
const view = (p: Period, marks = lebedevaMarks()) => ({ p, slots: buildSlots(p, marks) });
/** Доска «Пропуски в начале и в конце периода»: отметки только 8–17 октября. */
const edgeGaps = () =>
  lebedevaMarks().filter((m) => m.occurred_at >= "2026-10-08" && m.occurred_at < "2026-10-18");

describe("headline", () => {
  test("by day: time and level of the last mark", () => {
    const { p, slots } = view(period(14));
    expect(headline(p, slots, TODAY)).toEqual({ label: "Сегодня, 09:10", value: "Ровно" });
  });

  test("the last mark before today is named by its date", () => {
    const { p, slots } = view(period(14), edgeGaps());
    expect(headline(p, slots, TODAY)).toEqual({
      label: "Последняя отметка, 17 октября",
      value: "Неплохо",
    });
  });

  test("by week: the average of the last week", () => {
    const { p, slots } = view(period(90));
    expect(headline(p, slots, TODAY)).toEqual({
      label: "Среднее за неделю, 13–19 октября",
      value: "Ровно",
    });
  });

  test("no marks: the period and «Нет отметок»", () => {
    const p = resolvePeriod({ from: "2026-09-29", to: "2026-10-05" }, TODAY, "2026-09-29");
    expect(headline(p, buildSlots(p, []), TODAY)).toEqual({
      label: "29 сентября — 5 октября",
      value: "Нет отметок",
    });
  });
});

describe("coverage", () => {
  test("days with a mark out of the days of the period", () => {
    const { p, slots } = view(period(14));
    expect(coverage(p, slots)).toEqual({ count: "13 из 14 дней", note: "с отметкой настроения" });
  });

  test.each([30, 90])("with %i days only the days of observation count", (days) => {
    const { p, slots } = view(period(days));
    expect(coverage(p, slots)).toEqual({
      count: "26 из 28 дней",
      note: "с отметкой, наблюдение с 22.09",
    });
  });

  test("one day of observation", () => {
    const p = period(14, TODAY);
    expect(coverage(p, buildSlots(p, [mark(`${TODAY}T09:10:00+03:00`, 3)]))).toEqual({
      count: "1 из 1 дня",
      note: "с отметкой, наблюдение с 19.10",
    });
  });
});

describe("summary line", () => {
  test("by day: levels by count, then the days without a mark", () => {
    const { p, slots } = view(period(14));
    expect(summaryLine(p, slots)).toBe("За 14 дней: ровно 6, тяжело 5, неплохо 2, без отметки 1.");
  });

  test("equal counts go from the lower level up; days without a mark stay last", () => {
    const { p, slots } = view(period(14), edgeGaps());
    expect(summaryLine(p, slots)).toBe("За 14 дней: ровно 5, тяжело 2, неплохо 2, без отметки 5.");
  });

  test("a month with a zone counts the days of observation", () => {
    const { p, slots } = view(period(30));
    expect(summaryLine(p, slots)).toBe(
      "За 28 дней наблюдения: тяжело 13, ровно 9, очень тяжело 2, неплохо 2, без отметки 2.",
    );
  });

  // Средние не округляются: день со средним 2,5 — не «Ровно» и не «Тяжело», он между ними.
  test("a day with an average between levels is counted between them", () => {
    const marks = [
      mark("2026-10-13T09:10:00+03:00", 3),
      mark("2026-10-14T09:10:00+03:00", 3),
      mark("2026-10-15T09:10:00+03:00", 2),
      mark("2026-10-16T09:10:00+03:00", 2),
      mark("2026-10-16T21:10:00+03:00", 3, QUICK),
    ];
    const { p, slots } = view(period(7), marks);
    expect(summaryLine(p, slots)).toBe(
      "За 7 дней: ровно 2, тяжело 1, между «Тяжело» и «Ровно» 1, без отметки 3.",
    );
  });

  test("all marks on one level", () => {
    const marks = ["13", "14", "15", "16", "17", "18", "19"].map((d) =>
      mark(`2026-10-${d}T09:10:00+03:00`, 3),
    );
    const { p, slots } = view(period(7), marks);
    expect(summaryLine(p, slots)).toBe("За 7 дней: ровно 7.");
  });

  test("one mark: the curve comes with the second one", () => {
    const { p, slots } = view(period(14), [mark(`${TODAY}T09:10:00+03:00`, 3)]);
    expect(summaryLine(p, slots)).toBe("Кривая появится со второй отметкой.");
  });

  test("no marks: no line", () => {
    const { p, slots } = view(period(14), []);
    expect(summaryLine(p, slots)).toBeNull();
  });

  test("by week: the average from the first to the last week", () => {
    const { p, slots } = view(period(90));
    expect(summaryLine(p, slots)).toBe(
      "Точка — среднее за неделю. За 4 недели наблюдения среднее выросло с «Тяжело» до «Ровно».",
    );
  });

  test.each([
    [[3, 2], "снизилось с «Ровно» до «Тяжело»"],
    [[3, 3], "осталось на уровне «Ровно»"],
  ] as const)("by week, levels %j: %s", ([first, last], words) => {
    const marks = [mark("2026-10-07T09:00:00+03:00", first), mark(`${TODAY}T09:00:00+03:00`, last)];
    const p = resolvePeriod({ period: 90 }, TODAY, "2026-01-01");
    expect(summaryLine(p, buildSlots(p, marks))).toBe(
      `Точка — среднее за неделю. За 13 недель среднее ${words}.`,
    );
  });

  test.each([
    [[2, 3], 3, "выросло с уровня между «Тяжело» и «Ровно» до «Ровно»"],
    [[3, 4], 2, "снизилось с уровня между «Ровно» и «Неплохо» до «Тяжело»"],
    [[2, 3], 2.5, "осталось между «Тяжело» и «Ровно»"],
  ] as const)("by week, first %j then %d: %s", (first, last, words) => {
    const marks = [
      mark("2026-10-07T09:00:00+03:00", first[0]),
      mark("2026-10-07T21:00:00+03:00", first[1], QUICK),
      ...(last === 2.5
        ? [mark(`${TODAY}T09:00:00+03:00`, 2), mark(`${TODAY}T21:00:00+03:00`, 3, QUICK)]
        : [mark(`${TODAY}T09:00:00+03:00`, last)]),
    ];
    const p = resolvePeriod({ period: 90 }, TODAY, "2026-01-01");
    expect(summaryLine(p, buildSlots(p, marks))).toBe(
      `Точка — среднее за неделю. За 13 недель среднее ${words}.`,
    );
  });

  test("by week with one point: only what the point means", () => {
    const { p, slots } = view(period(90), [mark(`${TODAY}T09:10:00+03:00`, 3)]);
    expect(summaryLine(p, slots)).toBe("Точка — среднее за неделю.");
  });
});

describe("point details", () => {
  test("a day with one mark: date, level and kind with time", () => {
    const slots = buildSlots(period(7), [mark("2026-10-16T09:20:00+03:00", 4)]);
    expect(slots[3] && pointDetails(slots[3], false)).toEqual({
      title: "16 октября, пятница",
      value: "Неплохо",
      lines: [{ id: "e-2026-10-16T09:20:00+03:00", text: "Ежедневная отметка в 09:20" }],
    });
  });

  test("a day with several marks lists them all", () => {
    const slots = buildSlots(period(7), [
      mark("2026-10-18T21:05:00+03:00", 2),
      mark("2026-10-18T16:02:00+03:00", 3, QUICK),
    ]);
    expect(slots[5] && pointDetails(slots[5], false)).toEqual({
      title: "18 октября, воскресенье",
      value: "Между «Тяжело» и «Ровно»",
      lines: [
        { id: "e-2026-10-18T16:02:00+03:00", text: "Ровно — отметка настроения в 16:02" },
        { id: "e-2026-10-18T21:05:00+03:00", text: "Тяжело — ежедневная отметка в 21:05" },
      ],
    });
  });

  test("a week: its dates, the average and how many days have a mark", () => {
    const slots = buildSlots(period(90), lebedevaMarks());
    expect(slots[11] && pointDetails(slots[11], true)).toEqual({
      title: "6–12 октября",
      value: "Между «Тяжело» и «Ровно»",
      lines: [{ id: "2026-10-06", text: "Среднее за неделю, 6 отметок из 7" }],
    });
  });
});
