/**
 * Данные графика шкал (спека #13, разделы 4.1 и 4.4). Пороги и зоны — только из
 * определения шкалы в `knowledge/`; код их не задаёт.
 */
import type { Schemas } from "@compass/api-client";

type Result = Schemas["AssessmentOut"];
type Definition = Schemas["ScaleDefinitionOut"];

export type ScaleGroup = { scaleId: string; title: string; results: Result[] };
export type Zone = { min: number; max: number; code?: string; label: string };
export type AxisDomain = { from: number; to: number };

/** Ось дат не короче периода расписания шкал (14 дней, #111). */
export const MIN_AXIS_DAYS = 14;
const DAY_MS = 86_400_000;

/** Результаты по шкалам: внутри — от старых к новым; первой идёт шкала с последним заполнением. */
export function groupByScale(results: readonly Result[]): ScaleGroup[] {
  const groups = new Map<string, Result[]>();
  for (const r of [...results].sort((a, b) => a.completed_at.localeCompare(b.completed_at))) {
    groups.set(r.scale_id, [...(groups.get(r.scale_id) ?? []), r]);
  }
  return [...groups.entries()]
    .map(([scaleId, list]) => ({
      scaleId,
      title: list.at(-1)?.scale_title ?? scaleId,
      results: list,
    }))
    .sort((a, b) => lastAt(b).localeCompare(lastAt(a)));
}

/** Суммы шкалы в порядке определения; без определения — как в последнем результате. */
export function scoreIds(results: readonly Result[], definition: Definition | undefined): string[] {
  if (definition) {
    return definition.scale.scores.map((s) => s.id);
  }
  return Object.keys(results.at(-1)?.scores ?? {});
}

export function zones(definition: Definition | undefined, scoreId: string): Zone[] {
  const score = definition?.scale.scores.find((s) => s.id === scoreId);
  return (score?.bands ?? []).map((b) => ({
    min: b.min,
    max: b.max,
    code: b.code,
    label: b.label,
  }));
}

/**
 * Общая ось дат графиков (кадр 09): от первой точки до последней, но не короче
 * `MIN_AXIS_DAYS`. Первые точки одного дня стоят у правого края, а не по краям графика.
 */
export function axisDomain(times: readonly number[]): AxisDomain | null {
  if (times.length === 0) {
    return null;
  }
  const to = Math.max(...times);
  return { from: Math.min(Math.min(...times), to - MIN_AXIS_DAYS * DAY_MS), to };
}

/** Сообщение об ошибке определения шкалы: без него нет дорожек градаций (кадр 09в). */
export function definitionErrorText(names: readonly string[]): string {
  const list = names.join(" и ");
  return names.length > 1
    ? `Градации не показаны: не удалось загрузить определения шкал ${list}`
    : `Градации не показаны: не удалось загрузить определение шкалы ${list}`;
}

/** Есть результаты по другой версии шкалы: зоны текущего определения к ним — с оговоркой. */
export function outdated(results: readonly Result[], definition: Definition | undefined): boolean {
  return (
    definition !== undefined && results.some((r) => r.scale_version !== definition.scale.version)
  );
}

function lastAt(group: ScaleGroup): string {
  return group.results.at(-1)?.completed_at ?? "";
}
