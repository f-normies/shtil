import type { Schemas } from "@compass/api-client";
import { type KeyboardEvent, useState } from "react";
import type { AxisDomain } from "../../card/chart";
import { plural } from "../../card/documents";
import { type BandRange, monotonePath, shortBandLabel } from "../../card/levels";
import {
  agoText,
  longDay,
  type PointSlot,
  pieces,
  type Slot,
  scoreLabelAbove,
  shortDay,
  slotY,
  trackTrend,
  windowOf,
} from "../../card/track";
import { TrackPopover } from "./TrackPopover";

/** Ширина графика — колонка карточки 640 px без полей группы. */
const W = 608;
const PLOT = { left: 232, right: 592 };
const LANE = 32;
const TOP = 16;
const POPOVER = 248;

/**
 * «Уровни шкалы — полный» (спека #13, раздел 10.4; кадры 09, 09б, 09в): шапка с баллом и
 * сменой градации, дорожки градаций (тяжелее выше), монотонная кривая, пунктир через
 * пропуск, поповер. Tab — одна остановка, ←→ Home End — по точкам и пропускам, Esc
 * закрывает. Цвета нейтральные: красный и янтарный — только у сигналов.
 */
export function ScaleTrack({
  name,
  scaleDomain,
  bands,
  history,
  axis,
  definition,
  outdated,
  now,
}: {
  name: string;
  /** Область шкалы в подзаголовке: «Тревога, шкала 0–21» (кадр 09). */
  scaleDomain?: string;
  bands: readonly BandRange[];
  /** Все точки и пропуски шкалы: шапка и поповер читают всю историю, рисуется окно. */
  history: readonly Slot[];
  /** Общая ось дат всех графиков: `axisDomain` в разделе «Шкалы». */
  axis: AxisDomain;
  definition: Schemas["ScaleDefinitionOut"] | undefined;
  outdated: boolean;
  now: Date;
}) {
  // Позиция стрелок помнится, когда поповер закрыт: Esc или уход мыши не сбрасывают её.
  const [cursor, setCursor] = useState<number | null>(null);
  const [open, setOpen] = useState(false);
  const slots = windowOf(history);
  const sorted = [...bands].sort((a, b) => a.min - b.min);
  const max = sorted.length > 0 ? Math.max(...sorted.map((b) => b.max)) : null;
  const points = slots.filter((s): s is PointSlot => s.kind === "point");
  const last = history.findLast((s): s is PointSlot => s.kind === "point");
  const height = TOP * 2 + Math.max(0, sorted.length - 1) * LANE;
  const y = (lane: number) => TOP + (sorted.length - 1 - lane) * LANE;
  const x = (at: string) => {
    const span = axis.to - axis.from;
    const t = Date.parse(at);
    return span > 0 ? PLOT.left + ((t - axis.from) / span) * (PLOT.right - PLOT.left) : PLOT.right;
  };
  const xy = slots.map((s, i) => ({ x: x(s.at), y: slotY(slots, i, x, y) }));
  const { solid, dashed } = pieces(slots);
  const active = open ? cursor : null;
  const shown = active !== null ? slots[active] : undefined;
  const at = (s: Slot) => xy[slots.indexOf(s)] ?? { x: 0, y: 0 };
  const trend = trackTrend(history, now);
  /** Прошлое измерение по всей истории и пропуски между ним и слотом. */
  const previousOf = (slot: Slot) => {
    const before = history.slice(0, history.indexOf(slot));
    const previous = before.findLast((s): s is PointSlot => s.kind === "point");
    const missed = before
      .slice(previous ? before.indexOf(previous) + 1 : before.length)
      .map((s) => s.at);
    return { previous, missed };
  };
  const show = (i: number | null) => {
    setCursor(i);
    setOpen(i !== null);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const n = slots.length;
    const at = cursor ?? n - 1;
    const next =
      e.key === "ArrowRight"
        ? Math.min(at + 1, n - 1)
        : e.key === "ArrowLeft"
          ? Math.max(at - 1, 0)
          : e.key === "Home"
            ? 0
            : e.key === "End"
              ? n - 1
              : null;
    if (next !== null && n > 0) {
      e.preventDefault();
      show(next);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <article className="flex flex-col gap-3">
      <header className="flex flex-col gap-1">
        <p className="flex items-baseline justify-between gap-3">
          <span>
            <span className="text-[15px] font-semibold text-ink">{name}</span>
            {max !== null && (
              <span className="ml-2 text-[13px] text-ink-muted">
                {scaleDomain ? `${scaleDomain}, ` : ""}шкала 0–{max}
              </span>
            )}
          </span>
          {last && (
            <span className="text-[13px] text-ink-muted">
              {shortDay(last.at)}, {agoText(last.at, now)}
            </span>
          )}
        </p>
        {last && (
          <div className="flex items-center gap-4">
            <p className="flex w-20 shrink-0 items-baseline gap-1">
              <span className="text-[40px] leading-[44px] font-semibold text-ink">
                {last.value}
              </span>
              {max !== null && <span className="text-[13px] text-ink-muted">из {max}</span>}
            </p>
            <div className="min-w-0">
              <p className="text-[15px] font-semibold text-ink">{last.band}</p>
              <p className="text-[13px] text-ink-muted">{trend}</p>
            </div>
          </div>
        )}
      </header>

      <p className="sr-only">
        {last
          ? `${name}: последний балл ${last.value}${max !== null ? ` из ${max}` : ""}, ${last.band}. ${trend}.`
          : `${name}: отметок пока нет.`}
      </p>
      {/* biome-ignore lint/a11y/useSemanticElements: график с одной остановкой Tab и стрелками. */}
      <div
        role="group"
        tabIndex={slots.length > 0 ? 0 : -1}
        aria-label={`${name}, ${points.length} ${plural(points.length, ["измерение", "измерения", "измерений"])}`}
        onKeyDown={onKeyDown}
        onFocus={() => show(cursor ?? (slots.length > 0 ? slots.length - 1 : null))}
        onBlur={() => setOpen(false)}
        onMouseLeave={() => setOpen(false)}
        className="relative rounded-lg focus-visible:outline-2 focus-visible:outline-focus"
      >
        <svg
          width={W}
          height={height + 28}
          viewBox={`0 0 ${W} ${height + 28}`}
          className="overflow-visible"
          aria-hidden
        >
          {sorted.map((band, lane) => (
            <line
              key={band.label}
              x1={PLOT.left - 8}
              x2={W}
              y1={y(lane)}
              y2={y(lane)}
              className="stroke-line"
            />
          ))}
          {solid.map((run) => (
            <path
              key={run[0]?.at}
              d={monotonePath(run.map(at))}
              fill="none"
              className="stroke-primary"
              strokeWidth={2}
            />
          ))}
          {dashed.map(([a, b]) => (
            <line
              key={`${a.at}-${b.at}`}
              x1={at(a).x}
              y1={at(a).y}
              x2={at(b).x}
              y2={at(b).y}
              className="stroke-primary"
              strokeWidth={2}
              strokeDasharray="3 4"
            />
          ))}
          {slots.map((s, i) => {
            const p = xy[i] ?? { x: 0, y: 0 };
            const isLast = s === last;
            const before = previousOf(s).previous?.lane;
            const above = scoreLabelAbove(before, s.lane, sorted.length - 1);
            return (
              <g key={s.at}>
                {active === i && <circle cx={p.x} cy={p.y} r={11} className="fill-primary-soft" />}
                {s.kind === "gap" ? (
                  <circle
                    cx={p.x}
                    cy={p.y}
                    r={5}
                    className="fill-surface stroke-primary"
                    strokeWidth={2}
                  />
                ) : (
                  <circle cx={p.x} cy={p.y} r={isLast ? 5 : 3.5} className="fill-primary" />
                )}
                {s.kind === "point" && !(active === i) && (
                  <text
                    x={p.x}
                    y={above ? p.y - 12 : p.y + 20}
                    textAnchor="middle"
                    className={`text-[12px] ${isLast ? "fill-ink font-semibold" : "fill-ink-muted"}`}
                  >
                    {s.value}
                  </text>
                )}
                <text
                  x={p.x}
                  y={height + 22}
                  textAnchor="middle"
                  className="fill-ink-muted text-[12px]"
                >
                  {shortDay(s.at)}
                </text>
                {/* biome-ignore lint/a11y/noStaticElementInteractions: мышь дублирует стрелки группы. */}
                <circle
                  cx={p.x}
                  cy={p.y}
                  r={12}
                  className="cursor-default fill-transparent"
                  onMouseEnter={() => show(i)}
                />
              </g>
            );
          })}
        </svg>
        {/* Подписи дорожек — HTML: короткая градация (кадр 09), полностью — в подсказке. */}
        {sorted.map((band, lane) => (
          <p
            key={band.label}
            aria-hidden
            title={band.label}
            className={`absolute left-0 flex items-center gap-2 text-xs ${last?.lane === lane ? "font-semibold text-ink" : "text-ink-muted"}`}
            style={{ top: y(lane) - 8, width: PLOT.left - 16 }}
          >
            <span className="min-w-0 flex-1 truncate">{shortBandLabel(band)}</span>
            <span className="shrink-0">
              {band.min}–{band.max}
            </span>
          </p>
        ))}
        {points.length === 0 && (
          <div className="absolute inset-y-0 right-0 left-[232px] flex flex-col items-center justify-center bg-surface/80 text-center">
            <p className="text-[15px] font-semibold text-ink">Отметок пока нет</p>
            <p className="text-[13px] text-ink-muted">График появится после первой отметки</p>
          </div>
        )}
        {shown && active !== null && (
          <div
            className="absolute z-10 animate-fade-in rounded-xl bg-surface p-4 shadow-[0_12px_32px_-4px_rgb(16_26_32/12%),0_2px_6px_rgb(16_26_32/8%)]"
            style={{
              width: POPOVER,
              left: Math.min(Math.max((xy[active]?.x ?? 0) - POPOVER / 2, 0), W - POPOVER),
              top: (xy[active]?.y ?? 0) + 18,
            }}
          >
            <TrackPopover
              slot={shown}
              {...previousOf(shown)}
              name={name}
              max={max}
              definition={definition}
            />
          </div>
        )}
        <p aria-live="polite" className="sr-only">
          {shown &&
            (shown.kind === "gap"
              ? `${longDay(shown.at)}, отметки не было`
              : `${longDay(shown.at)}, ${shown.value}${max !== null ? ` из ${max}` : ""}, ${shown.band}`)}
        </p>
      </div>
      {outdated && (
        <p className="text-xs text-ink-muted">Часть результатов по прежней версии шкалы.</p>
      )}
    </article>
  );
}
