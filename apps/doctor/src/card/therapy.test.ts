import type { Schemas } from "@compass/api-client";
import { describe, expect, test } from "vitest";
import {
  ageText,
  checkLevel,
  checkWord,
  doseError,
  innChoices,
  isOffLabel,
  profileText,
  reactionText,
  regimenText,
  reportLevel,
  reportsOf,
  revealOffer,
  safetySummary,
  stopBeforeStart,
  stopFormOpen,
  timesText,
  years,
} from "./therapy";

type LabelRegimen = Schemas["LabelRegimen"];

const SOURCE = { source: "label", locator: "4.2", title: "ОХЛП", url: "https://example.org" };
const GAD: LabelRegimen = {
  form: "таблетки, 20 мг",
  dose_mg: 20,
  times_per_day: 2,
  schedule: "утром и вечером",
  duration_days: null,
  max_days: null,
  duration_text: "определяет врач",
  source: SOURCE,
};
const COURSE: LabelRegimen = {
  ...GAD,
  duration_days: 28,
  max_days: 42,
  duration_text: "4 недели, до 6",
};

describe("years", () => {
  test.each([
    ["1992-03-14", "2026-10-28", 34],
    ["2008-10-28", "2026-10-28", 18],
    ["2008-10-29", "2026-10-28", 17],
    ["2008-02-29", "2026-02-28", 17],
  ])("born %s, on %s → %i", (birth, today, expected) => {
    expect(years(birth, today)).toBe(expected);
  });
});

test.each([
  [1, "1 год"],
  [2, "2 года"],
  [5, "5 лет"],
  [11, "11 лет"],
  [21, "21 год"],
  [34, "34 года"],
  [112, "112 лет"],
])("ageText(%i) → %s", (n, text) => {
  expect(ageText(n)).toBe(text);
});

test("profile text joins age and sex, skips what is unknown", () => {
  expect(profileText({ birth_date: "1992-03-14", sex: "female" }, "2026-10-28")).toBe(
    "34 года, женщина",
  );
  expect(profileText({ birth_date: null, sex: "male" }, "2026-10-28")).toBe("мужчина");
  expect(profileText({ birth_date: null, sex: null }, "2026-10-28")).toBeNull();
});

test("regimen text names dose, frequency, schedule and duration", () => {
  expect(regimenText(GAD)).toBe(
    "по 20 мг 2 раза в сутки, утром и вечером; длительность: определяет врач",
  );
  expect(regimenText(COURSE)).toBe(
    "по 20 мг 2 раза в сутки, утром и вечером; длительность: 4 недели, до 6",
  );
});

describe("isOffLabel mirrors the server rule", () => {
  test.each([
    [GAD, { dose_mg: 20, times_per_day: 2, duration_days: null }, false],
    [GAD, { dose_mg: 20, times_per_day: 2, duration_days: 90 }, false],
    [GAD, { dose_mg: 40, times_per_day: 2, duration_days: null }, true],
    [GAD, { dose_mg: 20, times_per_day: 1, duration_days: null }, true],
    [COURSE, { dose_mg: 20, times_per_day: 2, duration_days: 42 }, false],
    [COURSE, { dose_mg: 20, times_per_day: 2, duration_days: 43 }, true],
    [COURSE, { dose_mg: 20, times_per_day: 2, duration_days: 14 }, true],
    [COURSE, { dose_mg: 20, times_per_day: 2, duration_days: null }, true],
  ])("%#", (label, chosen, expected) => {
    expect(isOffLabel(label, chosen)).toBe(expected);
  });
});

test.each([
  [1, "1 раз"],
  [2, "2 раза"],
  [4, "4 раза"],
  [5, "5 раз"],
])("timesText(%i) → %s", (n, text) => {
  expect(timesText(n)).toBe(text);
});

describe("safety checks", () => {
  const check = (id: string, status: "pass" | "fail" | "warn" | "manual", title = "Правило") => ({
    id,
    title,
    status,
    detail: "",
    source: null,
    actions: [],
  });

  test.each([
    [check("contra:maoi", "fail"), "обязательно"],
    [check("caution:serotonergic", "warn"), "желательно"],
    [check("contra:renal", "manual"), "уточните"],
    [check("age", "fail"), "не пройдена"],
    [check("age", "pass"), "пройдена"],
  ])("%o → %s", (c, word) => {
    expect(checkWord(c)).toBe(word);
  });

  // Уровень словом требует и значка уровня (#36): восьмиугольник или круг со стрелкой.
  test.each([
    [check("contra:maoi", "fail"), "mandatory"],
    [check("caution:serotonergic", "warn"), "desirable"],
    [check("indication", "warn"), "desirable"],
    [check("contra:renal", "manual"), null],
    [check("age", "fail"), null],
    [check("age", "pass"), null],
  ])("%o → level %s", (c, level) => {
    expect(checkLevel(c)).toBe(level);
  });

  test("summary names contraindications or says none were found", () => {
    expect(
      safetySummary([
        check("contra:maoi", "fail", "Ингибиторы МАО"),
        check("contra:renal", "pass"),
      ]),
    ).toBe("Противопоказания: Ингибиторы МАО");
    expect(safetySummary([check("contra:renal", "pass"), check("caution:x", "warn")])).toBe(
      "Противопоказаний не найдено",
    );
    expect(safetySummary([check("age", "pass")])).toBeNull();
  });

  test("summary does not say none were found while some items are not clarified", () => {
    const unclarified = "Противопоказаний по данным карты не найдено, уточните отмеченные пункты";
    expect(safetySummary([check("contra:renal", "manual"), check("contra:maoi", "pass")])).toBe(
      unclarified,
    );
    // Препарат без МНН: ингибитор МАО под незнакомым названием не исключён.
    expect(safetySummary([check("contra:maoi", "pass"), check("unmatched:деприм", "manual")])).toBe(
      unclarified,
    );
  });
});

describe("innChoices", () => {
  const match = (inn: string, trade_name: string | null = "Двоин") => ({
    inn,
    trade_name,
    reg_number: "ЛП-1",
  });

  test("similar names offer their INN with trade names", () => {
    const lookup = {
      check: "similar",
      matches: [match("Моклобемид", "Аурорикс"), match("Моклобемид", "Аурорикс®")],
      inn: null,
      inn_options: ["Моклобемид"],
    };
    expect(innChoices(lookup)).toEqual([
      { inn: "Моклобемид", label: "Аурорикс, Аурорикс® — Моклобемид" },
    ]);
  });

  test("every INN of an exact name is offered once, beyond the three shown matches", () => {
    // Ревью #76: варианты повторялись (ключ React) и обрезались до трёх.
    const lookup = {
      check: "registered",
      matches: [match("Первое"), match("Первое"), match("Второе")],
      inn: null,
      inn_options: ["Первое", "Второе", "Третье", "Четвёртое"],
    };
    expect(innChoices(lookup).map((c) => [c.inn, c.label])).toEqual([
      ["Первое", "Первое"],
      ["Второе", "Второе"],
      ["Третье", "Третье"],
      ["Четвёртое", "Четвёртое"],
    ]);
  });

  test("known INN or nothing to choose gives no choices", () => {
    const known = [match("Пирлиндол"), match("Пирлиндол")];
    expect(
      innChoices({ check: "registered", matches: known, inn: "Пирлиндол", inn_options: [] }),
    ).toEqual([]);
    expect(
      innChoices({ check: "inactive", matches: [match("")], inn: null, inn_options: [] }),
    ).toEqual([]);
    expect(innChoices({ check: "not_found", matches: [], inn: null, inn_options: [] })).toEqual([]);
  });
});

describe("stopBeforeStart", () => {
  // Сервер отвечает 422, если препарат отменён раньше, чем начат (ревью #76).
  test.each([
    ["2026-10-10", "2026-10-09", true],
    ["2026-10-10", "2026-10-10", false],
    ["2026-10-10", "2026-10-11", false],
    ["", "2026-10-09", false],
    [null, "2026-10-09", false],
    ["2026-10-10", "", false],
    ["2026-10-10", null, false],
  ])("started %s, stopped %s → %s", (started, stopped, expected) => {
    expect(stopBeforeStart(started, stopped)).toBe(expected);
  });
});

type ReactionReport = Schemas["ReactionReportOut"];

function reactionReport(
  id: string,
  prescriptionId: string,
  levels: ("mandatory" | "desirable")[],
): ReactionReport {
  return {
    id,
    prescription_id: prescriptionId,
    reactions: levels.map((level, i) => ({
      id: `r${i}`,
      title: `Реакция ${i}`,
      patient_title: `реакция ${i}`,
      level,
    })),
    note: null,
    reported_at: "2026-10-20T09:00:00Z",
  };
}

describe("reaction reports", () => {
  test("reports of one prescription keep server order", () => {
    const reports = [
      reactionReport("a", "p1", ["desirable"]),
      reactionReport("b", "p2", ["desirable"]),
      reactionReport("c", "p1", ["mandatory"]),
    ];
    expect(reportsOf(reports, "p1").map((r) => r.id)).toEqual(["a", "c"]);
    expect(reportsOf(undefined, "p1")).toEqual([]);
  });

  // Уровень отметки — наибольший среди реакций, как у сигнала (спека #29, раздел 6).
  test.each([
    [["desirable"], "desirable"],
    [["desirable", "mandatory"], "mandatory"],
  ] as const)("levels %j → %s", (levels, expected) => {
    expect(reportLevel(reactionReport("a", "p1", [...levels]))).toBe(expected);
  });

  test("reaction shows patient words when they differ from the term", () => {
    expect(
      reactionText({
        id: "t",
        title: "Тризм",
        patient_title: "сводит челюсть",
        level: "mandatory",
      }),
    ).toBe("Тризм — «сводит челюсть»");
    expect(
      reactionText({ id: "s", title: "Запор", patient_title: "запор", level: "desirable" }),
    ).toBe("Запор");
  });
});

// D3-10: доза проверяется явно, нативная подсказка `min` в интерфейсе не видна.
describe("doseError", () => {
  test.each([
    [20, null],
    [0.5, null],
    [0, "Доза должна быть больше нуля"],
    [-5, "Доза должна быть больше нуля"],
    [Number.NaN, "Укажите дозу числом"],
    [Number.POSITIVE_INFINITY, "Укажите дозу числом"],
  ])("dose %s → %s", (dose, expected) => {
    expect(doseError(dose)).toBe(expected);
  });
});

// D3-2: форма «Причина отмены» — только у действующего назначения.
describe("stopFormOpen", () => {
  test.each([
    ["active", true, true],
    ["active", false, false],
    ["stopped", true, false],
    ["completed", true, false],
  ] as const)("status %s, stopping %s → %s", (status, stopping, expected) => {
    expect(stopFormOpen(status, stopping)).toBe(expected);
  });
});

// M-5: готовое предложение прокручивается в начало экрана, фокус — на заголовке без прыжка.
test("revealOffer scrolls smoothly to the start and focuses without a second scroll", () => {
  const calls: unknown[] = [];
  revealOffer({
    scrollIntoView: (options) => calls.push(["scroll", options]),
    focus: (options) => calls.push(["focus", options]),
  });
  expect(calls).toEqual([
    ["scroll", { behavior: "smooth", block: "start" }],
    ["focus", { preventScroll: true }],
  ]);
});
