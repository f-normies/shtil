import type { Schemas } from "@compass/api-client";
import { describe, expect, test } from "vitest";
import { axisDomain, definitionErrorText, groupByScale, outdated, scoreIds, zones } from "./chart";
import { assessment } from "./fixtures";

function score(value: number, label = "Зона"): Schemas["AssessmentScoreOut"] {
  return {
    value,
    band: {
      code: "z",
      label,
      source: { id: "s", locator: "с. 1", title: "И", url: "https://e", supplementary: false },
    },
  };
}

function definition(version = 1): Schemas["ScaleDefinitionOut"] {
  const band = (min: number, max: number, label: string) => ({
    min,
    max,
    code: label,
    label,
    source: { source: "s", locator: "с. 1" },
  });
  return {
    sources: [],
    scale: {
      id: "test-mood",
      version,
      title: "Тестовая шкала настроения",
      instruction: "",
      purpose: "monitoring",
      respondent: "patient",
      status: "reviewed",
      license: { allowed: true, terms: "", source: { source: "s", locator: "" } },
      validation: [],
      items: [],
      scores: [
        { id: "anxiety", items: [], bands: [band(0, 9, "Низкая"), band(10, 21, "Высокая")] },
        { id: "total", items: [], bands: [band(0, 4, "Нет"), band(5, 27, "Есть")] },
      ],
    },
  } as Schemas["ScaleDefinitionOut"];
}

describe("groupByScale", () => {
  test("groups results per scale, oldest first, scale with the latest result first", () => {
    const results = [
      assessment({ id: "m2", scale_id: "mood", completed_at: "2026-10-10T09:00:00Z" }),
      assessment({
        id: "a1",
        scale_id: "anx",
        scale_title: "Тревога",
        completed_at: "2026-10-12T09:00:00Z",
      }),
      assessment({ id: "m1", scale_id: "mood", completed_at: "2026-10-01T09:00:00Z" }),
    ];

    const groups = groupByScale(results);

    expect(groups.map((g) => [g.scaleId, g.title, g.results.map((r) => r.id)])).toEqual([
      ["anx", "Тревога", ["a1"]],
      ["mood", "Тестовая шкала настроения", ["m1", "m2"]],
    ]);
  });
});

describe("scoreIds", () => {
  test("follow the scale definition order", () => {
    const results = [assessment({ scores: { total: score(1), anxiety: score(2) } })];

    expect(scoreIds(results, definition())).toEqual(["anxiety", "total"]);
  });

  test("fall back to the latest result without a definition", () => {
    const results = [assessment({ scores: { total: score(1), extra: score(2) } })];

    expect(scoreIds(results, undefined)).toEqual(["total", "extra"]);
  });
});

describe("zones and outdated", () => {
  test("zones come from bands of the chosen score", () => {
    expect(zones(definition(), "total")).toEqual([
      { min: 0, max: 4, code: "Нет", label: "Нет" },
      { min: 5, max: 27, code: "Есть", label: "Есть" },
    ]);
    expect(zones(undefined, "total")).toEqual([]);
  });

  test("marks results made with another scale version", () => {
    const results = [assessment({ scale_version: 1 }), assessment({ scale_version: 2 })];

    expect(outdated(results, definition(2))).toBe(true);
    expect(outdated(results.slice(1), definition(2))).toBe(false);
    expect(outdated(results, undefined)).toBe(false);
  });
});

// D3-3: две первые точки с разницей в минуты не растягиваются на всю ширину.
describe("axisDomain: общая ось дат не короче периода расписания", () => {
  const DAY = 86_400_000;

  test("одна точка — ось 14 дней, точка у правого края", () => {
    const at = Date.parse("2026-10-27T10:00:00Z");

    expect(axisDomain([at])).toEqual({ from: at - 14 * DAY, to: at });
  });

  test("две точки с разницей в минуты — обе у правого края оси 14 дней", () => {
    const first = Date.parse("2026-10-27T10:00:00Z");
    const second = Date.parse("2026-10-27T10:03:00Z");

    expect(axisDomain([first, second])).toEqual({ from: second - 14 * DAY, to: second });
  });

  test("точки за три месяца — ось от первой до последней", () => {
    const first = Date.parse("2026-07-27T10:00:00Z");
    const last = Date.parse("2026-10-27T10:00:00Z");

    expect(axisDomain([first, Date.parse("2026-09-01T10:00:00Z"), last])).toEqual({
      from: first,
      to: last,
    });
  });

  test("без точек — ось пустая", () => {
    expect(axisDomain([])).toBeNull();
  });
});

// D3-11: ошибка определения шкалы показана словами, как ошибка расписания.
describe("definitionErrorText", () => {
  test.each([
    [["PHQ-9"], "Градации не показаны: не удалось загрузить определение шкалы PHQ-9"],
    [
      ["PHQ-9", "GAD-7"],
      "Градации не показаны: не удалось загрузить определения шкал PHQ-9 и GAD-7",
    ],
  ])("%j", (names, text) => {
    expect(definitionErrorText(names)).toBe(text);
  });
});
