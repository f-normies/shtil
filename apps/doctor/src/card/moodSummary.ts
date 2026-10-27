/**
 * Тексты графика настроения (кадры 06–06д, доска состояний графика): итог над графиком,
 * строка под ним и поповер точки. Уровень всегда написан словом (аннотация, «Доступность»).
 */
import {
  type Day,
  dayMonth,
  dayOf,
  daysBetween,
  plural,
  rangeText,
  shortDate,
  timeOf,
} from "./days";
import {
  chartPoints,
  dayWithWeekday,
  isWeekly,
  levelBand,
  levelWord,
  markKind,
  type Period,
  type Slot,
} from "./mood";

type Headline = { label: string; value: string };
type Details = { title: string; value: string; lines: { id: string; text: string }[] };

const lower = (text: string) => text.charAt(0).toLowerCase() + text.slice(1);
const hasZone = (period: Period) => period.observedFrom > period.start;
const observedDays = (period: Period) => daysBetween(period.observedFrom, period.end) + 1;

/**
 * Итог над графиком. По дням — последняя отметка: время, если она сегодня, иначе дата.
 * По неделям — среднее последней недели с отметками. Без отметок — период и «Нет отметок».
 */
export function headline(period: Period, slots: readonly Slot[], today: Day): Headline {
  const points = chartPoints(slots);
  const lastPoint = points.at(-1);
  if (!lastPoint) {
    return { label: rangeText(period.start, period.end), value: "Нет отметок" };
  }
  const { slot } = lastPoint;
  if (isWeekly(period)) {
    return {
      label: `Среднее за неделю, ${rangeText(slot.start, slot.end)}`,
      value: levelWord(slot.value ?? 0),
    };
  }
  const last = slot.marks.at(-1);
  if (!last) {
    return { label: rangeText(period.start, period.end), value: "Нет отметок" };
  }
  const day = dayOf(last.occurred_at);
  return {
    label:
      day === today
        ? `Сегодня, ${timeOf(last.occurred_at)}`
        : `Последняя отметка, ${dayMonth(day)}`,
    value: levelWord(last.mood_level),
  };
}

/** «13 из 14 дней с отметкой настроения»; при зоне — только дни наблюдения. */
export function coverage(period: Period, slots: readonly Slot[]): { count: string; note: string } {
  const total = observedDays(period);
  const marked = slots.reduce((sum, s) => sum + s.markedDays, 0);
  // После «из» — родительный падеж: «из 1 дня», «из 21 дня», «из 14 дней».
  const days = plural(total, "дня", "дней", "дней");
  return {
    count: `${marked} из ${total} ${days}`,
    note: hasZone(period)
      ? `с отметкой, наблюдение с ${shortDate(period.observedFrom)}`
      : "с отметкой настроения",
  };
}

/**
 * Строка под графиком. По дням — сколько дней на каждом уровне: сначала частые, при равенстве
 * — от нижнего уровня, дни без отметки — последними. По неделям — как изменилось среднее
 * от первой недели к последней.
 */
export function summaryLine(period: Period, slots: readonly Slot[]): string | null {
  const points = chartPoints(slots);
  const first = points[0];
  const last = points.at(-1);
  if (!first || !last) {
    return null;
  }
  if (isWeekly(period)) {
    if (first === last) {
      return "Точка — среднее за неделю.";
    }
    const weeks = slots.filter((s) => s.observed).length;
    const span = `За ${weeks} ${plural(weeks, "неделю", "недели", "недель")}`;
    return `Точка — среднее за неделю. ${span}${zoneSuffix(period)} среднее ${trend(
      first.slot.value ?? 0,
      last.slot.value ?? 0,
    )}.`;
  }
  if (first === last) {
    return "Кривая появится со второй отметкой.";
  }
  const total = observedDays(period);
  // День со средним между уровнями считается «между»: округление сдвигало бы его вверх.
  const counts = new Map<number, number>();
  for (const p of points) {
    const band = levelBand(p.slot.value ?? 0);
    counts.set(band, (counts.get(band) ?? 0) + 1);
  }
  const parts = [...counts.entries()]
    .sort(([la, ca], [lb, cb]) => cb - ca || la - lb)
    .map(([level, count]) => `${lower(levelWord(level))} ${count}`);
  const missed = total - points.length;
  if (missed > 0) {
    parts.push(`без отметки ${missed}`);
  }
  const span = `За ${total} ${plural(total, "день", "дня", "дней")}${zoneSuffix(period)}`;
  return `${span}: ${parts.join(", ")}.`;
}

function zoneSuffix(period: Period): string {
  return hasZone(period) ? " наблюдения" : "";
}

/** Среднее первой и последней недели по полосам уровней, без округления (`levelBand`). */
function trend(from: number, to: number): string {
  const a = levelBand(from);
  const b = levelBand(to);
  if (a === b) {
    return Number.isInteger(b)
      ? `осталось на уровне «${levelWord(b)}»`
      : `осталось ${lower(levelWord(b))}`;
  }
  const verb = b > a ? "выросло" : "снизилось";
  return `${verb} с ${levelOf(a)} до ${levelOf(b)}`;
}

/** «с «Тяжело»», «с уровня между «Тяжело» и «Ровно»». */
const levelOf = (band: number): string =>
  Number.isInteger(band) ? `«${levelWord(band)}»` : `уровня ${lower(levelWord(band))}`;

/** Поповер точки: день — все отметки дня; неделя — среднее и сколько дней с отметкой. */
export function pointDetails(slot: Slot, weekly: boolean): Details {
  const value = levelWord(slot.value ?? 0);
  if (weekly) {
    const marks = plural(slot.markedDays, "отметка", "отметки", "отметок");
    return {
      title: rangeText(slot.start, slot.end),
      value,
      lines: [
        { id: slot.start, text: `Среднее за неделю, ${slot.markedDays} ${marks} из ${slot.days}` },
      ],
    };
  }
  const only = slot.marks.length === 1;
  return {
    title: dayWithWeekday(slot.start),
    value,
    lines: slot.marks.map((m) => {
      const kind = `${markKind(m)} в ${timeOf(m.occurred_at)}`;
      return {
        id: m.entry_id,
        text: only ? kind : `${levelWord(m.mood_level)} — ${lower(kind)}`,
      };
    }),
  };
}
