import type { Schemas } from "@compass/api-client";
import { describe, expect, test } from "vitest";
import {
  computedText,
  type GuidanceKind,
  groupClaims,
  guidanceCaption,
  guidanceView,
  isLongQuote,
  isSlow,
  LONG_QUOTE,
  levelsSpoken,
  levelsText,
  nextPollStart,
  POLL_LIMIT_MS,
  POLL_MS,
  pollInterval,
  sourceText,
  splitQuote,
} from "./guidance";

type Guidance = Schemas["GuidanceOut"];
type Fragment = Schemas["FragmentRef"];
type Claim = Schemas["Claim"];

const KR: Fragment = {
  id: "00000000-0000-0000-0000-000000000001",
  key: "kr-gad-2024/doc_3/4",
  source_id: "kr-gad-2024",
  title: "Генерализованное тревожное расстройство",
  version: "457_3",
  section: "doc_3",
  section_title: "3. Лечение",
  uur: "A",
  udd: 1,
  status: "active",
};
const LABEL: Fragment = {
  ...KR,
  id: "00000000-0000-0000-0000-000000000002",
  key: "aviandr-ohlp/4.2/1",
  source_id: "aviandr-ohlp",
  title: "АВИАНДР (маритупирдин): общая характеристика лекарственного препарата",
  version: "2026-01",
  section: "4.2",
  section_title: "Режим дозирования и способ применения",
  uur: null,
  udd: null,
};
const CLAIM: Claim = {
  statement: "Препараты первой линии при ГТР — антидепрессанты группы СИОЗС.",
  quote: "Рекомендуется назначение антидепрессантов из группы СИОЗС",
  fragment: KR,
};
const GAD = [{ id: "kr-gad-2024", title: "Генерализованное тревожное расстройство" }];
const DONE: Guidance = {
  status: "done",
  stale: false,
  claims: [CLAIM],
  computed_at: "2026-10-19T06:40:00Z",
  guidelines: GAD,
  catalog_drugs: ["Авиандр"],
  profile: { diagnoses: ["F41.1"], sex: "female", age_group: "18–64" },
};

// Строки таблицы раздела 6 спеки #107: состояние, текст пометки и кнопка.
describe("guidanceView: state per row of spec #107, section 6", () => {
  const rows: [string, Guidance, boolean, GuidanceKind, string | null, string | null][] = [
    [
      "нет диагнозов",
      { ...DONE, status: null, claims: [], computed_at: null, guidelines: [] },
      false,
      "no-diagnoses",
      "Добавьте диагноз, чтобы получить варианты по клиническим рекомендациям.",
      null,
    ],
    [
      "считается",
      { ...DONE, status: "pending", claims: [], computed_at: null },
      false,
      "pending",
      "Готовим справку по клиническим рекомендациям",
      null,
    ],
    [
      "считается дольше 3 минут",
      { ...DONE, status: "pending", claims: [], computed_at: null },
      true,
      "slow",
      "Справка ещё считается",
      "refresh",
    ],
    ["готово", DONE, false, "done", null, null],
    ["ответа нет", { ...DONE, claims: [] }, false, "empty", "В базе знаний ответа нет.", null],
    [
      "КР нет, справочник пуст",
      { ...DONE, claims: [], guidelines: [], catalog_drugs: [] },
      false,
      "no-guidelines",
      "По диагнозам пациента клинических рекомендаций в базе нет.",
      null,
    ],
    [
      "КР нет, ответ по инструкциям",
      { ...DONE, claims: [{ ...CLAIM, fragment: LABEL }], guidelines: [] },
      false,
      "no-guidelines",
      "По диагнозам пациента клинических рекомендаций в базе нет.",
      null,
    ],
    [
      "устарело, новая считается",
      { ...DONE, status: "pending", stale: true },
      false,
      "stale",
      "Справка по прежним данным пациента. Новая считается",
      null,
    ],
    [
      "устарело, новая считается дольше 3 минут",
      { ...DONE, status: "pending", stale: true },
      true,
      "stale",
      "Справка по прежним данным пациента. Новая ещё считается",
      "refresh",
    ],
    [
      "устарело, пересчёт упал",
      { ...DONE, status: "failed", stale: true },
      false,
      "stale",
      "Справка по прежним данным пациента. Новую посчитать не удалось",
      "retry",
    ],
    [
      "ошибка",
      { ...DONE, status: "failed", claims: [], computed_at: null },
      false,
      "failed",
      "Справку посчитать не удалось.",
      "retry",
    ],
  ];

  test.each(rows)("%s", (_, guidance, slow, kind, text, action) => {
    const view = guidanceView(guidance, slow);
    expect(view.kind).toBe(kind);
    expect(view.notice?.text ?? null).toBe(text);
    expect(view.notice?.action ?? null).toBe(action);
  });

  test("ready answer shows its claims without a notice", () => {
    expect(guidanceView(DONE, false).claims).toEqual([CLAIM]);
  });

  test("stale answer stays on screen under the notice", () => {
    const view = guidanceView({ ...DONE, status: "failed", stale: true }, false);
    expect(view.claims).toEqual([CLAIM]);
  });

  test("answer by drug labels is shown below the no-guidelines notice", () => {
    const claim = { ...CLAIM, fragment: LABEL };
    const view = guidanceView({ ...DONE, claims: [claim], guidelines: [] }, false);
    expect(view.claims).toEqual([claim]);
  });

  const PENDING: Guidance = { ...DONE, status: "pending", claims: [], computed_at: null };
  test.each([
    ["pending", PENDING, false, true],
    ["pending longer than 3 minutes", PENDING, true, false],
    ["stale", { ...DONE, status: "pending", stale: true }, false, false],
    ["done", DONE, false, false],
  ] as const)("skeleton only while the first answer is computed: %s", (_, guidance, slow, on) => {
    expect(guidanceView(guidance, slow).skeleton).toBe(on);
  });
});

describe("guidanceCaption: decision 17", () => {
  test.each([
    [
      ["Авиандр"],
      "Справка по клиническим рекомендациям. Назначить через систему можно только препараты " +
        "справочника: Авиандр.",
    ],
    [
      ["Авиандр", "Ципралекс"],
      "Справка по клиническим рекомендациям. Назначить через систему можно только препараты " +
        "справочника: Авиандр, Ципралекс.",
    ],
    [
      [],
      "Справка по клиническим рекомендациям. Справочник препаратов системы пока пуст: " +
        "назначить через систему ничего нельзя.",
    ],
  ])("%j", (drugs, caption) => {
    expect(guidanceCaption(drugs)).toBe(caption);
  });
});

// Решение 11: опрос раз в 5 с только при `pending`, через 3 минуты — остановка.
describe("polling", () => {
  test("constants follow decision 11", () => {
    expect(POLL_MS).toBe(5_000);
    expect(POLL_LIMIT_MS).toBe(180_000);
  });

  test.each([
    ["done", "done", 1_000, 1_000, false],
    ["failed", "failed", 1_000, 1_000, false],
    ["no diagnoses", null, 1_000, 1_000, false],
    ["no data yet", undefined, null, 1_000, false],
    ["pending, start not known yet", "pending", null, 1_000, POLL_MS],
    ["pending, just started", "pending", 1_000, 1_000, POLL_MS],
    ["pending, 1 ms before the limit", "pending", 1_000, 1_000 + POLL_LIMIT_MS - 1, POLL_MS],
    ["pending, at the limit", "pending", 1_000, 1_000 + POLL_LIMIT_MS, false],
  ] as const)("pollInterval: %s", (_, status, since, at, interval) => {
    expect(pollInterval(status, since, at)).toBe(interval);
  });

  test.each([
    ["pending, at the limit", "pending", 1_000, 1_000 + POLL_LIMIT_MS, true],
    ["pending, before the limit", "pending", 1_000, POLL_LIMIT_MS, false],
    ["pending, start not known yet", "pending", null, POLL_LIMIT_MS * 2, false],
    ["done long after the start", "done", 1_000, POLL_LIMIT_MS * 2, false],
  ] as const)("isSlow: %s", (_, status, since, at, slow) => {
    expect(isSlow(status, since, at)).toBe(slow);
  });

  test.each([
    ["first pending answer starts the clock", null, "pending", 5_000, 5_000],
    ["later pending answers keep it", 1_000, "pending", 5_000, 1_000],
    ["cached answer before the first fetch does not start it", null, "pending", null, null],
    ["done resets it", 1_000, "done", 5_000, null],
    ["failed resets it", 1_000, "failed", 5_000, null],
    ["no diagnoses resets it", 1_000, null, 5_000, null],
  ] as const)("nextPollStart: %s", (_, since, status, at, next) => {
    expect(nextPollStart(since, status, at)).toBe(next);
  });
});

describe("levels: only fragments with УУР or УДД show them", () => {
  test.each([
    ["both", { uur: "A", udd: 1 }, "УУР A, УДД 1"],
    ["only УУР", { uur: "C", udd: null }, "УУР C"],
    ["only УДД", { uur: null, udd: 3 }, "УДД 3"],
    ["none", { uur: null, udd: null }, null],
  ] as const)("levelsText: %s", (_, levels, text) => {
    expect(levelsText({ ...KR, ...levels })).toBe(text);
  });

  test.each([
    [
      { uur: "A", udd: 1 },
      "уровень убедительности рекомендаций A, уровень достоверности доказательств 1",
    ],
    [{ uur: "B", udd: null }, "уровень убедительности рекомендаций B"],
    [{ uur: null, udd: null }, null],
  ] as const)("levelsSpoken %j", (levels, text) => {
    expect(levelsSpoken({ ...KR, ...levels })).toBe(text);
  });
});

describe("sourceText: «{документ}, {раздел}»", () => {
  const ids = ["kr-gad-2024"];
  test.each([
    [
      "guideline of the profile",
      KR,
      ids,
      "КР «Генерализованное тревожное расстройство», 3. Лечение",
    ],
    [
      "guideline outside the profile, with levels",
      KR,
      [],
      "КР «Генерализованное тревожное расстройство», 3. Лечение",
    ],
    [
      "drug label: section number before its title",
      LABEL,
      ids,
      "АВИАНДР (маритупирдин): общая характеристика лекарственного препарата, " +
        "4.2. Режим дозирования и способ применения",
    ],
    [
      "title already starts with the number",
      { ...LABEL, section_title: "4.2. Режим дозирования" },
      ids,
      "АВИАНДР (маритупирдин): общая характеристика лекарственного препарата, 4.2. Режим дозирования",
    ],
  ] as const)("%s", (_, fragment, guidelineIds, text) => {
    expect(sourceText(fragment, guidelineIds)).toBe(text);
  });
});

describe("splitQuote: quote highlighted in the fragment text", () => {
  test("finds the quote as written in the text", () => {
    expect(splitQuote("Начало. Рекомендуется КПТ. Конец.", "Рекомендуется КПТ")).toEqual({
      before: "Начало. ",
      quote: "Рекомендуется КПТ",
      after: ". Конец.",
    });
  });

  test("quote not in the text — no highlight", () => {
    expect(splitQuote("Начало. Конец.", "Рекомендуется КПТ")).toBeNull();
  });

  test("empty quote — no highlight", () => {
    expect(splitQuote("Начало. Конец.", "")).toBeNull();
  });
});

// Аннотация кадра 10б: длинная цитата — до 6 строк и «Показать полностью».
test.each([
  ["short", "Рекомендуется КПТ", false],
  ["at the limit", "а".repeat(LONG_QUOTE), false],
  ["longer than 6 lines", "а".repeat(LONG_QUOTE + 1), true],
])("isLongQuote: %s", (_, quote, long) => {
  expect(isLongQuote(quote)).toBe(long);
});

test("computedText: «Посчитано» with day, month and time", () => {
  expect(computedText("2026-10-19T06:40:00Z", "Europe/Moscow")).toBe("Посчитано 19.10, 09:40");
});

// M-6 / D3-4: одна цитата у нескольких утверждений показана один раз, ключи групп разные.
describe("groupClaims", () => {
  const second: Claim = { ...CLAIM, statement: "Второе утверждение по той же цитате." };
  const other: Claim = { ...CLAIM, quote: "Другая цитата", statement: "Третье утверждение." };
  const label: Claim = { ...CLAIM, fragment: LABEL };

  test("same fragment and quote → one group with both statements, in order", () => {
    expect(groupClaims([CLAIM, other, second])).toEqual([
      {
        key: `${KR.id}/${CLAIM.quote}`,
        claim: CLAIM,
        statements: [CLAIM.statement, second.statement],
      },
      { key: `${KR.id}/Другая цитата`, claim: other, statements: [other.statement] },
    ]);
  });

  test("same quote from different fragments stays apart", () => {
    const groups = groupClaims([CLAIM, label]);
    expect(groups.map((g) => g.key)).toEqual([
      `${KR.id}/${CLAIM.quote}`,
      `${LABEL.id}/${CLAIM.quote}`,
    ]);
  });

  test("repeated statement is not duplicated", () => {
    expect(groupClaims([CLAIM, CLAIM])[0]?.statements).toEqual([CLAIM.statement]);
  });

  test("keys are unique", () => {
    const keys = groupClaims([CLAIM, second, other, label, CLAIM]).map((g) => g.key);
    expect(new Set(keys).size).toBe(keys.length);
  });
});
