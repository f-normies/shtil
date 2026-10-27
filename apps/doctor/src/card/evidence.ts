/**
 * Данные сигнала по правилу словами (спека #13, раздел 10.3; кадр «Врач v4 · 05»):
 * «PHQ-9, пункт 9: ответ «Несколько дней», 1 балл. Сигнал 19.10, 09:12». Пункт и ответ —
 * из определения шкалы; без него — коды как есть.
 */
import type { Schemas } from "@compass/api-client";
import type { Reason } from "./alerts";
import { plural } from "./documents";
import { shortScaleName } from "./levels";

const AT = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

export function evidenceText(
  reason: Reason,
  definition: Schemas["ScaleDefinitionOut"] | undefined,
  firedAt: string | null,
): string {
  const scale = definition ? shortScaleName(definition.scale.title) : reason.scale;
  const points = `${reason.value} ${plural(reason.value, ["балл", "балла", "баллов"])}`;
  const items = definition?.scale.items ?? [];
  const index = items.findIndex((i) => i.id === reason.key);
  const answer = items[index]?.options.find((o) => o.value === reason.value)?.text;
  const what = reason.isScore
    ? `${scale}: ${points}`
    : index >= 0
      ? `${scale}, пункт ${index + 1}: ${answer ? `ответ «${answer}», ` : ""}${points}`
      : `${scale}, ${reason.key}: ${points}`;
  return firedAt ? `${what}. Сигнал ${AT.format(new Date(firedAt))}.` : `${what}.`;
}
