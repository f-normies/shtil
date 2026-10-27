import type { Schemas } from "@compass/api-client";

type Condition = Schemas["Condition"];
type Trigger = Schemas["HelpTriggerOut"];
type Scale = Pick<Schemas["Scale"], "id" | "scores">;

const OPS: Record<NonNullable<Condition["op"]>, (actual: number, value: number) => boolean> = {
  ">=": (actual, value) => actual >= value,
  ">": (actual, value) => actual > value,
  "<=": (actual, value) => actual <= value,
  "<": (actual, value) => actual < value,
  "==": (actual, value) => actual === value,
};

/**
 * Открывает ли ответ экран помощи (спека #57, раздел 6). Повторяет
 * `clin_compass_knowledge.rules.evaluate`: сравнение без данных ложно. Данные — только
 * отправленная шкала: ответы на пункты и суммы из определения. Порогов в коде нет.
 */
export function evaluateHelpTriggers(
  triggers: readonly Trigger[],
  scale: Scale,
  answers: Readonly<Record<string, number>>,
): boolean {
  const values: Record<string, number> = { ...answers };
  for (const score of scale.scores) {
    if (score.items.every((item) => Object.hasOwn(answers, item))) {
      values[score.id] = score.items.reduce((sum, item) => sum + (answers[item] ?? 0), 0);
    }
  }
  return triggers.some((trigger) => holds(trigger.when, scale.id, values));
}

function holds(condition: Condition, scaleId: string, values: Record<string, number>): boolean {
  if (condition.any) return condition.any.some((child) => holds(child, scaleId, values));
  if (condition.all) return condition.all.every((child) => holds(child, scaleId, values));
  const key = condition.item ?? condition.score;
  if (condition.scale !== scaleId || !key || !condition.op || condition.value == null) return false;
  const actual = values[key];
  return actual !== undefined && OPS[condition.op](actual, condition.value);
}
