import type { Schemas } from "@compass/api-client";
import { describe, expect, test } from "vitest";
import { evaluateHelpTriggers } from "./triggers";

type Trigger = Schemas["HelpTriggerOut"];
type Scale = Pick<Schemas["Scale"], "id" | "scores">;

// Условие правила phq9-suicidal-thoughts из knowledge/rules: пункт 9 от 1 балла.
const PHQ9_ITEM_9: Trigger = {
  rule_id: "phq9-suicidal-thoughts",
  rule_version: 2,
  when: { any: [{ scale: "phq-9", item: "q9", op: ">=", value: 1 }] },
};
const PHQ9: Scale = {
  id: "phq-9",
  scores: [{ id: "total", items: ["q1", "q9"], bands: [] }],
};

describe("help triggers", () => {
  // Случаи — `cases` правила phq9-suicidal-thoughts (zolotareva-phq9-2023, с. 121, пункт 9).
  test.each([
    ["«совсем нет» does not open the screen", { q1: 3, q9: 0 }, false],
    ["«несколько дней» opens the screen", { q1: 0, q9: 1 }, true],
    ["«почти каждый день» opens the screen", { q1: 0, q9: 3 }, true],
    ["a severe total without item 9 does not open the screen", { q1: 3, q9: 0 }, false],
    ["no answer to item 9 does not open the screen", { q1: 3 }, false],
  ])("%s", (_name, answers, opens) => {
    expect(evaluateHelpTriggers([PHQ9_ITEM_9], PHQ9, answers)).toBe(opens);
  });

  test("a score leaf compares the sum of the score items", () => {
    const trigger: Trigger = {
      ...PHQ9_ITEM_9,
      when: { all: [{ scale: "phq-9", score: "total", op: ">", value: 3 }] },
    };

    expect(evaluateHelpTriggers([trigger], PHQ9, { q1: 2, q9: 1 })).toBe(false);
    expect(evaluateHelpTriggers([trigger], PHQ9, { q1: 3, q9: 1 })).toBe(true);
  });

  test("a rule over two scales does not open the screen without the other answers", () => {
    // Ответов другой шкалы у PWA нет: такое правило откроет экран ответ сервера.
    const trigger: Trigger = {
      ...PHQ9_ITEM_9,
      when: {
        all: [
          { scale: "phq-9", item: "q9", op: ">=", value: 1 },
          { scale: "gad-7", score: "total", op: ">=", value: 15 },
        ],
      },
    };

    expect(evaluateHelpTriggers([trigger], PHQ9, { q1: 0, q9: 3 })).toBe(false);
  });

  test("operators follow the knowledge package", () => {
    const leaf = (op: ">=" | ">" | "<=" | "<" | "==") => ({
      ...PHQ9_ITEM_9,
      when: { scale: "phq-9", item: "q9", op, value: 2 },
    });

    expect(
      (["<", "<=", "==", ">=", ">"] as const).map((op) =>
        evaluateHelpTriggers([leaf(op)], PHQ9, { q9: 2 }),
      ),
    ).toEqual([false, true, true, true, false]);
  });

  test("a trigger of another scale is ignored", () => {
    const other: Trigger = {
      ...PHQ9_ITEM_9,
      when: { scale: "hamd-17", item: "q3", op: ">=", value: 1 },
    };

    expect(evaluateHelpTriggers([other], PHQ9, { q3: 4, q9: 0 })).toBe(false);
  });
});
