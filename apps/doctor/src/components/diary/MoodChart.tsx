import { type KeyboardEvent, useId, useState } from "react";
import { type Day, rangeText } from "../../card/days";
import {
  axisLabels,
  CHART,
  type ChartPoint,
  chartPoints,
  currentLevel,
  curvePath,
  gapSpans,
  isWeekly,
  levelWord,
  levelY,
  moveActive,
  observationZone,
  type Period,
  type Slot,
  showsDots,
  slotX,
  tableRows,
} from "../../card/mood";
import { pointDetails } from "../../card/moodSummary";
import { MoodPopover } from "./MoodPopover";

const LEVELS = [5, 4, 3, 2, 1] as const;
/** Нижний край полосы дня и зоны: дорожка «Очень тяжело» и половина шага. */
const FIELD_BOTTOM = CHART.bottom + 8;
const NAV_KEYS = ["ArrowLeft", "ArrowRight", "Home", "End"];

/**
 * «Врач v4/График настроения» (кадры 06–06г, доска состояний; аннотация «График настроения:
 * правила для разработки»): кривая curveMonotoneX по уровням 1–5, пропуски пунктиром,
 * зона до начала наблюдения, поповер точки, клавиатура и скрытая таблица для чтения с экрана.
 */
export function MoodChart({
  period,
  slots,
  today,
  onOpenEntry,
  onShowWeek,
}: {
  period: Period;
  slots: Slot[];
  today: Day;
  onOpenEntry: (entryId: string, at: string) => void;
  onShowWeek: ((slot: Slot) => void) | null;
}) {
  const id = useId();
  const [active, setActive] = useState<number | null>(null);
  const weekly = isWeekly(period);
  const step = CHART.plot / slots.length;
  const points = chartPoints(slots);
  const path = curvePath(points);
  const gaps = gapSpans(points);
  const zone = observationZone(period, slots);
  const labels = axisLabels(slots, today);
  const withWeekday = labels.some((l) => l.weekday !== null);
  const zoneRowY = withWeekday ? CHART.height + 12 : CHART.weekdayY + 12;
  const height = zone && !zone.inside && withWeekday ? CHART.height + 16 : CHART.height;
  const last = points.at(-1);
  const current = currentLevel(points);
  const shown = points.find((p) => p.index === active);
  const details = shown ? pointDetails(shown.slot, weekly) : null;
  const trackStart = zone ? zone.x2 : CHART.left;

  function open(point: ChartPoint) {
    if (weekly) {
      onShowWeek?.(point.slot);
      return;
    }
    const mark = point.slot.marks.at(-1);
    if (mark) {
      onOpenEntry(mark.entry_id, mark.occurred_at);
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    // Esc закрывает поповер и с его кнопки: фокус возвращается на график.
    if (event.key === "Escape") {
      setActive(null);
      if (event.target !== event.currentTarget) {
        event.currentTarget.focus();
      }
      return;
    }
    if (event.target !== event.currentTarget) {
      return;
    }
    if (event.key === "Enter" && shown) {
      event.preventDefault();
      open(shown);
    } else if (NAV_KEYS.includes(event.key)) {
      event.preventDefault();
      setActive(moveActive(points, active, event.key));
    }
  }

  return (
    // biome-ignore lint/a11y/useSemanticElements: fieldset — для полей формы; здесь группа графика с клавиатурой
    <div
      role="group"
      className="relative rounded-md focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-focus"
      style={{ width: CHART.width, height }}
      tabIndex={points.length > 0 ? 0 : -1}
      aria-label={`График настроения, ${rangeText(period.start, period.end)}. Стрелки — по дням с отметкой, Enter — открыть, Esc — закрыть подсказку.`}
      onKeyDown={onKeyDown}
      onFocus={(e) => {
        if (e.target === e.currentTarget && active === null) {
          setActive(last?.index ?? null);
        }
      }}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget)) {
          setActive(null);
        }
      }}
      onMouseLeave={() => setActive(null)}
    >
      <svg aria-hidden width={CHART.width} height={height} className="block overflow-visible">
        <defs>
          <clipPath id={`${id}-solid`}>
            {solidSpans(gaps).map(([x1, x2]) => (
              <rect key={x1} x={x1} y={-8} width={x2 - x1} height={height + 16} />
            ))}
          </clipPath>
          <clipPath id={`${id}-gap`}>
            {gaps.map((g) => (
              <rect key={g.x1} x={g.x1} y={-8} width={g.x2 - g.x1} height={height + 16} />
            ))}
          </clipPath>
        </defs>

        {LEVELS.map((level) => (
          <g key={level}>
            <text
              x={0}
              y={levelY(level) + 4}
              fontSize={12}
              className={level === current ? "fill-ink font-semibold" : "fill-ink-muted"}
            >
              {levelWord(level)}
            </text>
            <line
              x1={trackStart}
              x2={CHART.width}
              y1={levelY(level) + 0.5}
              y2={levelY(level) + 0.5}
              className="stroke-line"
            />
          </g>
        ))}

        {zone && (
          <g>
            <rect
              x={zone.x1}
              y={0}
              width={zone.x2 - zone.x1}
              height={FIELD_BOTTOM}
              rx={6}
              className="fill-surface-sunken"
            />
            <text
              x={zone.inside ? (zone.x1 + zone.x2) / 2 : zone.x1}
              y={zone.inside ? levelY(3) + 4 : zoneRowY}
              textAnchor={zone.inside ? "middle" : "start"}
              fontSize={12}
              className="fill-ink-muted"
            >
              {zone.label}
            </text>
          </g>
        )}

        {shown && (
          <line x1={shown.x} x2={shown.x} y1={0} y2={FIELD_BOTTOM} className="stroke-line-strong" />
        )}

        {path && (
          <g fill="none" className="stroke-primary" strokeWidth={2}>
            <path
              d={path}
              clipPath={`url(#${id}-solid)`}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            {gaps.length > 0 && (
              <path d={path} clipPath={`url(#${id}-gap)`} strokeDasharray="4 3" />
            )}
          </g>
        )}

        {points.map((p) => (
          <Dot
            key={p.index}
            point={p}
            kind={p === shown ? "active" : p === last ? "last" : "regular"}
            visible={p === shown || p === last || showsDots(slots.length)}
          />
        ))}

        {labels.map((l) => (
          <g key={l.index} fontSize={12} textAnchor="middle">
            <text
              x={l.x}
              y={CHART.dateY + 12}
              className={l.today ? "fill-primary-text font-semibold" : "fill-ink-muted"}
            >
              {l.text}
            </text>
            {l.weekday && (
              <text
                x={l.x}
                y={CHART.weekdayY + 12}
                className={l.today ? "fill-primary-text font-semibold" : "fill-ink-subtle"}
              >
                {l.weekday}
              </text>
            )}
          </g>
        ))}

        {slots.map((s) => (
          // biome-ignore lint/a11y/noStaticElementInteractions: полоса дня — цель наведения мышью; с клавиатуры то же даёт группа графика
          <rect
            key={s.index}
            x={slotX(s.index, slots.length) - step / 2}
            y={0}
            width={step}
            height={FIELD_BOTTOM}
            fill="transparent"
            onMouseEnter={() => setActive(points.some((p) => p.index === s.index) ? s.index : null)}
          />
        ))}
      </svg>

      {shown && details && (
        <MoodPopover
          x={shown.x}
          y={shown.y}
          height={height}
          title={details.title}
          value={details.value}
          lines={details.lines}
          action={weekly ? (onShowWeek ? "Показать неделю" : null) : "Открыть запись"}
          onAction={() => open(shown)}
        />
      )}
      <p aria-live="polite" className="sr-only">
        {details
          ? `${details.title}: ${details.value}. ${details.lines.map((l) => l.text).join(". ")}`
          : ""}
      </p>

      <table className="sr-only">
        <caption>Отметки настроения, {rangeText(period.start, period.end)}</caption>
        <thead>
          <tr>
            <th scope="col">Дата</th>
            <th scope="col">Уровень</th>
            <th scope="col">Вид отметки</th>
          </tr>
        </thead>
        <tbody>
          {tableRows(slots).map((row) => (
            <tr key={row.entryId}>
              <td>
                {row.date}, {row.time}
              </td>
              <td>{row.level}</td>
              <td>{row.kind}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** «Врач v4/График/Точка»: обычная 6 px, последняя 10 px, наведение 10 px с ореолом 22 px. */
function Dot({
  point,
  kind,
  visible,
}: {
  point: ChartPoint;
  kind: "regular" | "last" | "active";
  visible: boolean;
}) {
  if (!visible) {
    return null;
  }
  const r = kind === "regular" ? 3 : 5;
  return (
    <g>
      {kind === "active" && (
        <circle cx={point.x} cy={point.y} r={11} className="fill-primary" fillOpacity={0.16} />
      )}
      <circle cx={point.x} cy={point.y} r={r + 2} className="fill-surface" />
      <circle cx={point.x} cy={point.y} r={r} className="fill-primary" />
    </g>
  );
}

/** Участки сплошной кривой: всё поле, кроме пропусков. */
function solidSpans(gaps: readonly { x1: number; x2: number }[]): [number, number][] {
  const spans: [number, number][] = [];
  let from = -8;
  for (const g of gaps) {
    spans.push([from, g.x1]);
    from = g.x2;
  }
  spans.push([from, CHART.width + 8]);
  return spans;
}
