import { ChevronRight } from "lucide-react";
import { CHART } from "../../card/mood";

const WIDTH = 264;
/** Облако стоит над точкой: зазор до центра точки — ореол наведения 11 px и хвостик. */
const ABOVE_POINT = 18;

/**
 * Поповер точки графика настроения (доска состояний: «Наведение на точку — день» и
 * «…— неделя, 3 месяца»). Хвостик над точкой; у края облако сдвигается внутрь графика.
 */
export function MoodPopover({
  x,
  y,
  height,
  title,
  value,
  lines,
  action,
  onAction,
}: {
  x: number;
  y: number;
  height: number;
  title: string;
  value: string;
  lines: { id: string; text: string }[];
  action: string | null;
  onAction: () => void;
}) {
  const left = Math.min(Math.max(x - WIDTH / 2, 0), CHART.width - WIDTH);
  return (
    <div
      className="absolute z-10 flex flex-col gap-0.5 rounded-xl bg-surface px-4 pt-3 pb-3 shadow-[0_12px_16px_rgb(16_26_32/12%),0_2px_3px_rgb(16_26_32/8%)] motion-safe:animate-rise-in motion-reduce:animate-fade-in"
      style={{ left, bottom: height - y + ABOVE_POINT, width: WIDTH }}
    >
      <p className="text-xs leading-4 text-ink-muted">{title}</p>
      <p className="text-[15px] leading-5 font-semibold text-ink">{value}</p>
      {lines.map((line) => (
        <p key={line.id} className="text-[13px] leading-[18px] text-ink-muted">
          {line.text}
        </p>
      ))}
      {action && (
        <button
          type="button"
          onClick={onAction}
          className="mt-1.5 inline-flex items-center gap-1 self-start rounded-md text-sm leading-5 font-semibold text-primary-text hover:text-primary-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
        >
          {action}
          <ChevronRight aria-hidden className="size-4" />
        </button>
      )}
      <span
        aria-hidden
        className="absolute -bottom-1.5 size-3 rotate-45 bg-surface"
        style={{ left: x - left - 6 }}
      />
    </div>
  );
}
