/**
 * «Моя терапия» (спека #29, раздел 7): тексты схемы приёма и отметки нежелательной реакции.
 * Чистые функции, без React.
 */

/** «по 20 мг 2 раза в сутки, утром и вечером». */
export function regimenText(item: {
  dose_mg: number;
  times_per_day: number;
  schedule: string;
}): string {
  const n = item.times_per_day;
  const few = n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14);
  return `по ${item.dose_mg} мг ${n} ${few ? "раза" : "раз"} в сутки, ${item.schedule}`;
}

/** `ends_on` — первый день без приёма; пациенту показываем последний день приёма. */
export function lastDayIso(endsOn: string): string {
  const day = new Date(`${endsOn}T00:00:00Z`);
  day.setUTCDate(day.getUTCDate() - 1);
  return day.toISOString().slice(0, 10);
}

const MOSCOW_DAY = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Moscow" });

/** День момента по московскому календарю, как считает сервер: `started_at` хранится в UTC. */
export function moscowDayIso(at: string): string {
  return MOSCOW_DAY.format(new Date(at));
}

/** Отметить или снять реакцию; выбранные идут в порядке перечня инструкции. */
export function toggleReaction(
  selected: readonly string[],
  id: string,
  order: readonly string[],
): string[] {
  const next = new Set(selected);
  if (next.has(id)) {
    next.delete(id);
  } else {
    next.add(id);
  }
  return order.filter((r) => next.has(r));
}

/** Текст ошибки отправки по статусу ответа; без ответа — нет связи. */
export function reportErrorText(status: number | undefined): string {
  if (status === 409) {
    return "Назначение уже не действует. Обновите страницу.";
  }
  if (status === 422) {
    return "Не удалось принять отметку. Обновите страницу и выберите реакции из списка.";
  }
  return "Не удалось отправить. Проверьте связь и попробуйте ещё раз.";
}

/** Статус из ответа об ошибке в формате problem+json; нет ответа — `undefined`. */
export function errorStatus(error: unknown): number | undefined {
  if (typeof error !== "object" || error === null || !("status" in error)) {
    return undefined;
  }
  return typeof error.status === "number" ? error.status : undefined;
}
