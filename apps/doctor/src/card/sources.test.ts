import type { Schemas } from "@compass/api-client";
import { describe, expect, test } from "vitest";
import { alert, trigger } from "./fixtures";
import {
  alertBasis,
  checkBasis,
  claimBasis,
  guidanceApplicability,
  instructionBasis,
  keyed,
  refKey,
  refsText,
  regimenBasis,
  sourceQuery,
} from "./sources";

/** Время в тестах — как есть: формат даты проверяют тесты `format`. */
const at = (iso: string) => `[${iso}]`;

const KR = {
  source: "kr-depression-2024",
  locator: "раздел 2.5",
  title: "Депрессия",
  url: "https://cr",
};
const SCALE = { source: "phq9-validation", locator: "с. 39", title: "PHQ-9", url: "https://v" };

describe("alertBasis", () => {
  test("rule signal: rule with version, all its documents, values and the firing time", () => {
    const basis = alertBasis(
      alert({
        title: "Вероятная депрессия",
        rule_version: 2,
        sources: [KR, SCALE],
        triggers: [trigger({ at: "2026-10-18T09:00:00Z", evidence: { "phq-9": { total: 16 } } })],
      }),
      at,
    );

    expect(basis).toEqual({
      heading: "Правило «Вероятная депрессия», версия 2",
      applicability: ["PHQ-9 · сумма: 16", "Срабатывание [2026-10-18T09:00:00Z]"],
      refs: [
        {
          source: "kr-depression-2024",
          title: "Депрессия",
          locator: "раздел 2.5",
          url: "https://cr",
        },
        { source: "phq9-validation", title: "PHQ-9", locator: "с. 39", url: "https://v" },
      ],
    });
  });

  test("rule signal keeps the item answer and the safety rule detail", () => {
    const basis = alertBasis(
      alert({
        sources: [KR],
        triggers: [trigger({ evidence: { "phq-9": { "phq9-9": 1 }, detail: "СКФ 25" } })],
      }),
      at,
    );

    expect(basis?.applicability).toEqual([
      "PHQ-9 · phq9-9: 1",
      "СКФ 25",
      "Срабатывание [2026-10-18T08:12:00Z]",
    ]);
  });

  test("AI signal: sign kind, quotes and the record, documents of all sign kinds", () => {
    const basis = alertBasis(
      alert({
        origin_type: "ai_run",
        rule_id: null,
        rule_version: null,
        title: "Мысли о смерти в тексте пациента",
        triggers: [
          trigger({
            at: "2026-10-18T09:00:00Z",
            evidence: {
              source_type: "diary",
              signs: [
                { kind: "thoughts", quote: "не хочу жить", quote_found: true },
                { kind: "intent", quote: "выдумано", quote_found: false },
              ],
              sources: { thoughts: [KR], intent: [KR, { source: "old", locator: "с. 1" }] },
            },
          }),
        ],
      }),
      at,
    );

    expect(basis).toEqual({
      heading: "Вид признака «Мысли о смерти в тексте пациента»",
      applicability: [
        "«не хочу жить»",
        "«выдумано» — цитата не найдена в тексте",
        "дневник, [2026-10-18T09:00:00Z]",
      ],
      refs: [
        {
          source: "kr-depression-2024",
          title: "Депрессия",
          locator: "раздел 2.5",
          url: "https://cr",
        },
        { source: "old", title: "old", locator: "с. 1" },
      ],
    });
  });

  test("rule signal shows the same document section once", () => {
    const basis = alertBasis(alert({ sources: [KR, SCALE, { ...KR }] }), at);

    expect(basis?.refs.map((r) => r.source)).toEqual(["kr-depression-2024", "phq9-validation"]);
  });

  test("signal without documents has no basis", () => {
    expect(alertBasis(alert({ sources: [] }), at)).toBeNull();
  });
});

type Check = Schemas["CheckOut"];
const LABEL = {
  source: "aviandr-ohlp",
  locator: "4.1: генерализованное тревожное расстройство",
  title: "АВИАНДР: ОХЛП",
  url: "https://eaeu",
};

describe("checkBasis", () => {
  test("check with a source: its title, detail and document", () => {
    const check: Check = {
      id: "indication",
      title: "Показание",
      status: "pass",
      detail: "F41.1 у пациентки",
      source: LABEL,
      actions: [],
    };

    expect(checkBasis(check)).toEqual({
      heading: "Проверка «Показание»",
      applicability: ["F41.1 у пациентки"],
      refs: [
        {
          source: "aviandr-ohlp",
          title: "АВИАНДР: ОХЛП",
          locator: "4.1: генерализованное тревожное расстройство",
          url: "https://eaeu",
        },
      ],
    });
  });

  test("check without a source has no basis", () => {
    const check: Check = {
      id: "x",
      title: "X",
      status: "manual",
      detail: "",
      source: null,
      actions: [],
    };

    expect(checkBasis(check)).toBeNull();
  });
});

describe("label bases", () => {
  const option = {
    trade_name: "Авиандр",
    regimen: { source: { ...LABEL, locator: "4.2, «Лечение ГТР»" } },
  } as Schemas["OptionOut"];

  test("regimen opens the dosing section of the label", () => {
    expect(regimenBasis(option)).toEqual({
      heading: "Схема по инструкции «Авиандр»",
      applicability: [],
      refs: [{ ...LABEL, locator: "4.2, «Лечение ГТР»" }],
    });
  });

  test("instruction opens its own section", () => {
    const instruction = { text: "Принимать до еды", source: { ...LABEL, locator: "4.2" } };

    expect(instructionBasis(option, instruction)).toEqual({
      heading: "Указание по инструкции «Авиандр»",
      applicability: [],
      refs: [{ ...LABEL, locator: "4.2" }],
    });
  });
});

describe("claimBasis", () => {
  test("claim opens its fragment with the quote", () => {
    const claim = {
      statement: "Рекомендуется СИОЗС",
      quote: "рекомендуется назначать СИОЗС",
      fragment: {
        id: "f1",
        source_id: "kr-gad-2024",
        title: "ГТР",
        key: "k",
        section: "doc_3",
        section_title: "3.1 Медикаментозное лечение",
        status: "active",
        version: "457_3",
        uur: "A",
        udd: 1,
      },
    } as Schemas["Claim"];

    expect(claimBasis(claim, ["Пол: женский"])).toEqual({
      heading: "Утверждение справки",
      applicability: ["Пол: женский"],
      refs: [
        {
          source: "kr-gad-2024",
          title: "ГТР",
          locator: null,
          fragmentId: "f1",
          quote: claim.quote,
        },
      ],
    });
  });
});

describe("guidanceApplicability", () => {
  type Profile = NonNullable<Schemas["GuidanceOut"]["profile"]>;
  const profile = (overrides: Partial<Profile> = {}): Profile => ({
    diagnoses: ["F41.1", "F43.2"],
    sex: "female",
    age_group: "18–64",
    ...overrides,
  });

  test("profile of the shown answer: diagnoses, sex and age group in words", () => {
    expect(guidanceApplicability(profile(), false)).toEqual([
      "Диагнозы: F41.1, F43.2",
      "Пол: женский",
      "Возраст: 18–64 года",
    ]);
  });

  test.each([
    ["<18", "Возраст: младше 18 лет"],
    ["≥65", "Возраст: 65 лет и старше"],
    [null, "Возраст: не указан"],
  ] as const)("age group %s: %s", (group, line) => {
    expect(guidanceApplicability(profile({ age_group: group }), false)[2]).toBe(line);
  });

  test("unknown sex is said so", () => {
    expect(guidanceApplicability(profile({ sex: null }), false)[1]).toBe("Пол: не указан");
  });

  test("stale answer: the previous profile and a note about it", () => {
    expect(guidanceApplicability(profile({ diagnoses: ["F41.1"], sex: "male" }), true)).toEqual([
      "Диагнозы: F41.1",
      "Пол: мужской",
      "Возраст: 18–64 года",
      "Справка посчитана по прежним данным пациента",
    ]);
  });

  test("no answer, no profile lines", () => {
    expect(guidanceApplicability(null, false)).toEqual([]);
  });
});

describe("refsText", () => {
  test("documents with sections, each pair once", () => {
    const refs = [
      { source: "a", title: "Депрессия", locator: "раздел 2.5" },
      { source: "a", title: "Депрессия", locator: "раздел 2.5" },
      { source: "b", title: "PHQ-9", locator: "с. 39" },
    ];

    expect(refsText(refs)).toBe("«Депрессия», раздел 2.5; «PHQ-9», с. 39");
  });

  test("fragment reference without a locator shows only the document", () => {
    expect(refsText([{ source: "a", title: "ГТР", locator: null, fragmentId: "f" }])).toBe("«ГТР»");
  });
});

describe("sourceQuery", () => {
  test("fragment reference asks for the fragment", () => {
    const ref = { source: "a", title: "A", locator: null, fragmentId: "f1", quote: "q" };

    expect(sourceQuery(ref)).toEqual({ fragment_id: "f1" });
    expect(refKey(ref)).toBe("a#f1");
  });

  test("locator reference asks for the section", () => {
    const ref = { source: "a", title: "A", locator: "4.3" };

    expect(sourceQuery(ref)).toEqual({ locator: "4.3" });
    expect(refKey(ref)).toBe("a|4.3");
  });
});

describe("keyed", () => {
  test("repeated values get their own keys in order", () => {
    expect(keyed(["a", "b", "a", "a"], (v) => v)).toEqual([
      { item: "a", key: "a" },
      { item: "b", key: "b" },
      { item: "a", key: "a#2" },
      { item: "a", key: "a#3" },
    ]);
  });
});
