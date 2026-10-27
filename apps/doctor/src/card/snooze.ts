/**
 * Поповер «Отложить до…» (кадр 11б): проверка «другой даты» и размещение поповера
 * относительно кнопки. Чистые функции, без React.
 */

const DAY_MS = 86_400_000;
const EDGE = 8;
const GAP = 8;
const MONTH_DAY = new Intl.DateTimeFormat("ru-RU", {
  day: "numeric",
  month: "long",
  timeZone: "UTC",
});

export type Box = { top: number; bottom: number; left: number; right: number };
export type Size = { width: number; height: number };
export type Placement = { side: "below" | "above"; top: number; left: number };

function addDays(day: string, days: number): string {
  return new Date(Date.parse(`${day}T00:00:00Z`) + days * DAY_MS).toISOString().slice(0, 10);
}

function dayOf(day: string): string {
  return MONTH_DAY.format(new Date(`${day}T00:00:00Z`));
}

/**
 * Почему «другую дату» нельзя выбрать: раньше завтра или дальше предела `maxDays`.
 * Пустое поле — null: без даты кнопка и так неактивна. Даты — `YYYY-MM-DD` по Москве.
 */
export function snoozeDateError(date: string, today: string, maxDays: number): string | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    return null;
  }
  const first = addDays(today, 1);
  if (date < first) {
    return `Дата должна быть не раньше завтра, ${dayOf(first)}`;
  }
  const last = addDays(today, maxDays);
  if (date > last) {
    return `Отложить можно не дальше ${dayOf(last)}`;
  }
  return null;
}

/**
 * Где показать поповер: под кнопкой по центру; снизу мало места — над ней; не помещается
 * нигде — там, где места больше, не выше и не левее края экрана (8 px).
 */
export function placePopover(anchor: Box, panel: Size, viewport: Size): Placement {
  const below = viewport.height - anchor.bottom - GAP - EDGE;
  const above = anchor.top - GAP - EDGE;
  const side = panel.height <= below || below >= above ? "below" : "above";
  const top =
    side === "below" ? anchor.bottom + GAP : Math.max(EDGE, anchor.top - GAP - panel.height);
  const centered = (anchor.left + anchor.right) / 2 - panel.width / 2;
  const left = Math.max(EDGE, Math.min(centered, viewport.width - panel.width - EDGE));
  return { side, top, left };
}
