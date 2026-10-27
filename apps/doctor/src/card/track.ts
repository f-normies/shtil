/**
 * График шкалы в разделе «Шкалы» (спека #13, раздел 10.4; кадры «Врач v4 · 09», «09б»,
 * «09в»): точки измерений и пропуски плановых дат (#111), окно последних измерений,
 * участки кривой и подписи. Пропуск не рисуется ни нулём, ни улучшением: он только
 * отмечает дату. Чистые функции, без React.
 */
import type { Schemas } from "@compass/api-client";
import { plural } from "./documents";
import { type BandRange, bandTrend, laneOf } from "./levels";

type Result = Schemas["AssessmentOut"];

export type PointSlot = {
  kind: "point";
  at: string;
  lane: number;
  value: number;
  band: string;
  result: Result;
};
/** Пропуск: плановая дата без ответа. Дорожка — соседнего измерения, для раскладки. */
export type GapSlot = { kind: "gap"; at: string; lane: number };
export type Slot = PointSlot | GapSlot;

/** В окне графика — последние плановые измерения, пропуски считаются (кадр 09). */
export const WINDOW = 6;
const DAY_MS = 86_400_000;
const SHORT = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short" });

/** «19 окт» — без точки сокращения, как в макете. */
export function shortDay(iso: string): string {
  return SHORT.format(new Date(iso)).replace(".", "");
}

/**
 * Все точки и пропуски шкалы по возрастанию даты. Точка стоит на дате ответа, пропуск —
 * на плановой дате (`missed` из расписания). Без измерений пропусков не рисуем: дорожки
 * для них нет. Шапка, смена градации и поповер читают всю историю, окно — только
 * рисунок (`windowOf`).
 */
export function buildTrack({
  results,
  missed,
  scoreId,
  bands,
}: {
  results: readonly Result[];
  missed: readonly string[];
  scoreId: string;
  bands: readonly BandRange[];
}): Slot[] {
  const points: PointSlot[] = results.flatMap((r) => {
    const score = r.scores[scoreId];
    return score
      ? [
          {
            kind: "point" as const,
            at: r.completed_at,
            lane: laneOf(score.value, bands),
            value: score.value,
            band: score.band.label,
            result: r,
          },
        ]
      : [];
  });
  if (points.length === 0) {
    return [];
  }
  const gaps: GapSlot[] = missed.map((day) => ({ kind: "gap", at: `${day}T12:00:00`, lane: 0 }));
  const slots = [...points, ...gaps].sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
  return slots.map((s, i) => (s.kind === "gap" ? { ...s, lane: neighbourLane(slots, i) } : s));
}

/**
 * Окно рисунка — последние `WINDOW` плановых измерений. После долгого перерыва окно
 * растягивается до последнего измерения: без него график соврал бы, что отметок нет.
 */
export function windowOf<T extends Slot>(slots: readonly T[]): T[] {
  const lastPoint = slots.findLastIndex((s) => s.kind === "point");
  const start = Math.max(0, Math.min(slots.length - WINDOW, lastPoint));
  return slots.slice(start);
}

/** Пропущенные плановые даты шкалы из расписания (#111). */
export function missedOf(plans: readonly Schemas["ScalePlanOut"][], scaleId: string): string[] {
  return (plans.find((p) => p.scale_id === scaleId)?.planned ?? [])
    .filter((d) => d.status === "missed")
    .map((d) => d.planned_on);
}

/** Дорожка пропуска: последнего измерения до него, в начале — первого после. */
function neighbourLane(slots: readonly Slot[], index: number): number {
  const before = slots.slice(0, index).findLast((s) => s.kind === "point");
  const after = slots.slice(index + 1).find((s) => s.kind === "point");
  return (before ?? after)?.lane ?? 0;
}

/**
 * Участки кривой: сплошные — подряд идущие точки (от двух), пунктир — от точки до точки
 * через пропуски, а пропуски в начале и в конце — к ближайшей точке.
 */
export function pieces(slots: readonly Slot[]): { solid: PointSlot[][]; dashed: [Slot, Slot][] } {
  const solid: PointSlot[][] = [];
  const dashed: [Slot, Slot][] = [];
  let run: PointSlot[] = [];
  let lastPoint: PointSlot | undefined;
  let gapSince: Slot | undefined;
  for (const s of slots) {
    if (s.kind === "gap") {
      gapSince ??= lastPoint ?? s;
      if (run.length > 1) solid.push(run);
      run = [];
      continue;
    }
    if (gapSince) {
      dashed.push([gapSince, s]);
      gapSince = undefined;
    }
    run.push(s);
    lastPoint = s;
  }
  if (run.length > 1) solid.push(run);
  const tail = slots.at(-1);
  if (gapSince && tail && gapSince !== tail) dashed.push([gapSince, tail]);
  return { solid, dashed };
}

/** Смена градации словами в шапке графика. */
export function trackTrend(slots: readonly Slot[], now: Date): string {
  const points = slots.filter((s): s is PointSlot => s.kind === "point");
  const last = points.at(-1);
  const previous = points.at(-2);
  if (!last) {
    return "";
  }
  const tail = slots.at(-1);
  if (tail?.kind === "gap") {
    const base = !previous
      ? "Первое измерение"
      : previous.band !== last.band
        ? `${previous.band} → ${last.band}`
        : `${last.band}, без изменений`;
    return `${base}. ${shortDay(tail.at)} отметки не было`;
  }
  if (!previous) {
    return "Первое измерение";
  }
  const between = slots.slice(slots.indexOf(previous) + 1, slots.indexOf(last));
  if (between.length > 0 && previous.band !== last.band) {
    return `${previous.band} ${shortDay(previous.at)} → ${last.band} ${shortDay(last.at)}`;
  }
  return bandTrend(points, now, shortDay);
}

/** Изменение к прошлому измерению в поповере: «−3 балла к 12 окт, Умеренная → Лёгкая». */
export function deltaText(
  previous: { value: number; band: string; at: string },
  current: { value: number; band: string },
  missedBetween: readonly string[] = [],
): string {
  const diff = current.value - previous.value;
  const sign = diff > 0 ? "+" : diff < 0 ? "−" : "";
  const n = Math.abs(diff);
  const bands =
    previous.band === current.band ? "градация та же" : `${previous.band} → ${current.band}`;
  const gaps =
    missedBetween.length > 0 ? `. ${missedBetween.map(shortDay).join(", ")} отметки не было` : "";
  return `${sign}${n} ${plural(n, ["балл", "балла", "баллов"])} к ${shortDay(previous.at)}, ${bands}${gaps}`;
}

/** Давность последнего измерения в шапке: «сегодня», «3 дня назад», «неделю назад». */
export function agoText(iso: string, now: Date): string {
  const start = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((start(now) - start(new Date(iso))) / DAY_MS);
  if (days <= 0) return "сегодня";
  if (days === 1) return "вчера";
  if (days < 7) return `${days} ${plural(days, ["день", "дня", "дней"])} назад`;
  const weeks = Math.floor(days / 7);
  return weeks === 1
    ? "неделю назад"
    : `${weeks} ${plural(weeks, ["неделю", "недели", "недель"])} назад`;
}

const LONG_DAY = new Intl.DateTimeFormat("ru-RU", {
  day: "numeric",
  month: "long",
  weekday: "long",
});

/** Дата в поповере и озвучке: «12 октября, понедельник». */
export function longDay(iso: string): string {
  const parts = LONG_DAY.formatToParts(new Date(iso));
  const part = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${part("day")} ${part("month")}, ${part("weekday")}`;
}

/**
 * Подпись балла над точкой, если кривая приходит снизу или ровно; иначе под точкой.
 * На верхней дорожке — всегда под точкой: сверху её срежет край графика.
 */
export function scoreLabelAbove(
  beforeLane: number | undefined,
  lane: number,
  topLane: number,
): boolean {
  return lane !== topLane && (beforeLane === undefined || beforeLane <= lane);
}

/**
 * Высота слота: точка — на своей дорожке; пропуск между точками — на пунктире между ними,
 * в начале или в конце — на дорожке соседнего измерения.
 */
export function slotY(
  slots: readonly Slot[],
  index: number,
  x: (at: string) => number,
  y: (lane: number) => number,
): number {
  const s = slots[index];
  if (!s || s.kind === "point") {
    return y(s?.lane ?? 0);
  }
  const before = slots.slice(0, index).findLast((p) => p.kind === "point");
  const after = slots.slice(index + 1).find((p) => p.kind === "point");
  if (!before || !after) {
    return y(s.lane);
  }
  const [x0, x1] = [x(before.at), x(after.at)];
  const share = x1 > x0 ? (x(s.at) - x0) / (x1 - x0) : 0;
  return y(before.lane) + share * (y(after.lane) - y(before.lane));
}
