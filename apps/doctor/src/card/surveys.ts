import type { Schemas } from "@compass/api-client";
import { shortDay } from "./track";

type Setting = Schemas["ScaleSettingOut"];

const PERIOD_LABELS: Readonly<Record<number, string>> = {
  7: "Раз в неделю",
  14: "Раз в две недели",
};

/** Частота опроса словами (спека #180); период не из списка — числом дней. */
export function periodLabel(days: number): string {
  return PERIOD_LABELS[days] ?? `Раз в ${days} дней`;
}

/** Кто и когда изменил настройку шкалы; без изменений — значение по умолчанию. */
export function changedText(setting: Setting): string {
  if (!setting.updated_at) {
    return "По умолчанию";
  }
  const who = setting.updated_by_name ? `${setting.updated_by_name}, ` : "";
  return `Изменено: ${who}${shortDay(setting.updated_at)}`;
}

/** Период, общий для всех шкал расписания; у шкал разные периоды — `undefined`. */
export function commonPeriod(plans: readonly Schemas["ScalePlanOut"][]): number | undefined {
  const periods = new Set(plans.map((p) => p.period_days));
  return periods.size === 1 ? plans[0]?.period_days : undefined;
}
