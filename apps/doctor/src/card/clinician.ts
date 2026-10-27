import type { Schemas } from "@compass/api-client";

type Scale = Schemas["Scale"];

export type ClinicianTotal = {
  answered: number;
  total: number;
  /** Сумма первой оценки шкалы, когда отвечены все пункты. */
  sum: number | null;
  max: number;
  /** Градация из ключа шкалы; `null`, пока сумма не готова или вне диапазонов. */
  band: string | null;
};

/**
 * Итог формы врача по ходу заполнения (спека #94, форма): сколько пунктов отвечено, сумма
 * и градация по первой оценке определения. Результат в истории считает сервер.
 */
export function clinicianTotal(
  scale: Scale,
  answers: Readonly<Record<string, number>>,
): ClinicianTotal {
  const total = scale.items.length;
  const answered = scale.items.filter((i) => answers[i.id] !== undefined).length;
  const score = scale.scores[0];
  const counted = score ? scale.items.filter((i) => score.items.includes(i.id)) : [];
  const max = counted.reduce((acc, i) => acc + Math.max(0, ...i.options.map((o) => o.value)), 0);
  if (!score || answered < total) {
    return { answered, total, sum: null, max, band: null };
  }
  const sum = counted.reduce((acc, i) => acc + (answers[i.id] ?? 0), 0);
  const band = score.bands.find((b) => b.min <= sum && sum <= b.max)?.label ?? null;
  return { answered, total, sum, max, band };
}
