/**
 * «Уровни шкалы» (спека #13, раздел 10.4; компонент «Врач v4/Уровни шкалы»): дорожка
 * градации, смена градации словами и плавная монотонная кривая. Градации — только из
 * определения шкалы в `knowledge/`. Чистые функции, без React.
 */
import { plural } from "./documents";

export type BandRange = { min: number; max: number; code?: string; label: string };
export type BandPoint = { at: string; band: string };
export type XY = { x: number; y: number };

const DAY_MS = 86_400_000;
const DAY_MONTH = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit" });

/** Короткое имя шкалы — код в скобках названия: «Опросник … (PHQ-9)» → «PHQ-9». */
export function shortScaleName(title: string): string {
  return /\(([^()]+)\)\s*$/.exec(title)?.[1] ?? title;
}

/** Короткие подписи дорожек по коду градации (кадр 09); полное название — в подсказке. */
const SHORT_BANDS: Readonly<Record<string, string>> = {
  minimal: "Минимальная",
  mild: "Лёгкая",
  moderate: "Умеренная",
  "moderately-severe": "Умеренно тяжёлая",
  severe: "Тяжёлая",
};

/** Область шкалы в подзаголовке: «Тревога, шкала 0–21». Шкалы вне списка — без области. */
const SCALE_DOMAINS: Readonly<Record<string, string>> = {
  "phq-9": "Депрессия",
  "gad-7": "Тревога",
  "hamd-17": "Депрессия",
  hars: "Тревога",
};

export function shortBandLabel(band: { code?: string; label: string }): string {
  return (band.code && SHORT_BANDS[band.code]) || band.label;
}

export function scaleDomain(scaleId: string): string | undefined {
  return SCALE_DOMAINS[scaleId];
}

/** Номер дорожки градации: 0 — самая лёгкая, тяжелее выше. */
export function laneOf(value: number, bands: readonly BandRange[]): number {
  const sorted = [...bands].sort((a, b) => a.min - b.min);
  const index = sorted.findIndex((b) => value >= b.min && value <= b.max);
  if (index >= 0) {
    return index;
  }
  return value < (sorted[0]?.min ?? 0) ? 0 : sorted.length - 1;
}

/**
 * Пояснение строки «Показатель/Шкала»: «Умеренная → Лёгкая с 19.10» или «Лёгкая, без
 * изменений 2 недели». Длительность — от начала последней серии одной градации.
 * Точки — от старых к новым.
 */
export function bandTrend(
  points: readonly BandPoint[],
  now: Date,
  day: (iso: string) => string = dayMonth,
): string {
  const last = points.at(-1);
  if (!last) {
    return "";
  }
  const previous = points.at(-2);
  if (!previous) {
    return last.band;
  }
  if (previous.band !== last.band) {
    return `${previous.band} → ${last.band} с ${day(last.at)}`;
  }
  let start = points.length - 1;
  while (start > 0 && points[start - 1]?.band === last.band) {
    start -= 1;
  }
  const since = points[start]?.at ?? last.at;
  return `${last.band}, без изменений ${durationText(since, now, day)}`;
}

function dayMonth(iso: string): string {
  return DAY_MONTH.format(new Date(iso));
}

function durationText(since: string, now: Date, day: (iso: string) => string): string {
  const days = Math.floor((now.getTime() - Date.parse(since)) / DAY_MS);
  if (days >= 7) {
    const weeks = Math.floor(days / 7);
    return `${weeks} ${plural(weeks, ["неделю", "недели", "недель"])}`;
  }
  if (days >= 1) {
    return `${days} ${plural(days, ["день", "дня", "дней"])}`;
  }
  return `с ${day(since)}`;
}

/**
 * SVG-путь плавной монотонной кривой через точки (метод Фрича — Карлсона, как
 * `curveMonotoneX` в d3): на каждом участке кривая не выходит выше или ниже его концов.
 * Точки — по возрастанию x. Одна точка — пустой путь.
 */
export function monotonePath(points: readonly XY[]): string {
  if (points.length < 2) {
    return "";
  }
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const t = tangents(xs, ys);
  const parts = [`M${num(xs[0] ?? 0)},${num(ys[0] ?? 0)}`];
  for (let i = 0; i < xs.length - 1; i += 1) {
    const [x0 = 0, x1 = 0] = [xs[i], xs[i + 1]];
    const [y0 = 0, y1 = 0] = [ys[i], ys[i + 1]];
    const [t0 = 0, t1 = 0] = [t[i], t[i + 1]];
    const third = (x1 - x0) / 3;
    parts.push(
      `C${num(x0 + third)},${num(y0 + t0 * third)} ` +
        `${num(x1 - third)},${num(y1 - t1 * third)} ${num(x1)},${num(y1)}`,
    );
  }
  return parts.join("");
}

/** Наклоны касательных в точках: внутри — ограниченное среднее, на краях — по соседу. */
function tangents(xs: readonly number[], ys: readonly number[]): number[] {
  const n = xs.length;
  const h = (i: number) => (xs[i + 1] ?? 0) - (xs[i] ?? 0);
  const secant = (i: number) => (h(i) ? ((ys[i + 1] ?? 0) - (ys[i] ?? 0)) / h(i) : 0);
  if (n === 2) {
    return [secant(0), secant(0)];
  }
  const t = new Array<number>(n).fill(0);
  for (let i = 1; i < n - 1; i += 1) {
    const s0 = secant(i - 1);
    const s1 = secant(i);
    const p = (s0 * h(i) + s1 * h(i - 1)) / (h(i - 1) + h(i));
    t[i] =
      (Math.sign(s0) + Math.sign(s1)) * Math.min(Math.abs(s0), Math.abs(s1), 0.5 * Math.abs(p));
  }
  t[0] = (3 * secant(0) - (t[1] ?? 0)) / 2;
  t[n - 1] = (3 * secant(n - 2) - (t[n - 2] ?? 0)) / 2;
  return t;
}

function num(value: number): number {
  return Math.round(value * 100) / 100;
}
