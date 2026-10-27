import { Info } from "lucide-react";
import type { ReactNode } from "react";
import { type Day, rangeText } from "../../card/days";
import { diaryFailure } from "../../card/diary";
import {
  buildSlots,
  CHART,
  DEFAULT_DAYS,
  type MoodPoint,
  type Period,
  type PeriodSearch,
  type Slot,
} from "../../card/mood";
import { coverage, headline, summaryLine } from "../../card/moodSummary";
import { Section } from "../signals/Section";
import { MoodChart } from "./MoodChart";
import { PeriodSwitch } from "./PeriodSwitch";

/**
 * Группа «Настроение, 6–19 октября» (кадры 06–06д): переключатель периода, итог, график
 * и строка под ним. Загрузка — скелетон, ошибка — «Не удалось загрузить отметки настроения».
 */
export function MoodPanel({
  period,
  today,
  observationStart,
  moods,
  onPeriod,
  onOpenEntry,
  onShowWeek,
}: {
  period: Period;
  today: Day;
  observationStart: Day;
  moods: {
    data: MoodPoint[] | undefined;
    isError: boolean;
    error: unknown;
    refetch: () => unknown;
  };
  onPeriod: (search: PeriodSearch) => void;
  onOpenEntry: (entryId: string, at: string) => void;
  onShowWeek: (slot: Slot) => void;
}) {
  const slots = buildSlots(period, moods.data ?? []);
  const top = headline(period, slots, today);
  const days = coverage(period, slots);
  const summary = summaryLine(period, slots);
  const empty = moods.data !== undefined && moods.data.length === 0;
  const failure = diaryFailure(moods.error, "Не удалось загрузить отметки настроения");

  return (
    <Section title={`Настроение, ${rangeText(period.start, period.end)}`}>
      <div className="flex flex-col gap-4">
        <PeriodSwitch
          period={period}
          today={today}
          observationStart={observationStart}
          onChange={onPeriod}
        />
        {moods.isError ? (
          <ChartMessage slots={slots} period={period} today={today}>
            <Info aria-hidden className="size-4 text-ink-muted" />
            <span>{failure.text}</span>
            {failure.retry && (
              <TextButton onClick={() => void moods.refetch()}>Повторить</TextButton>
            )}
          </ChartMessage>
        ) : moods.data === undefined ? (
          <ChartSkeleton />
        ) : (
          <div
            key={`${period.start}-${period.end}`}
            className="flex flex-col gap-4 animate-fade-in"
          >
            <div className="flex items-end gap-4">
              <div className="min-w-0 flex-1">
                <p className="text-[13px] leading-[18px] text-ink-muted">{top.label}</p>
                <p className="text-[30px] leading-[34px] font-semibold tracking-[-0.6px] text-ink">
                  {top.value}
                </p>
              </div>
              <div className="shrink-0 text-right">
                <p className="text-[15px] leading-5 font-semibold text-ink">{days.count}</p>
                <p className="text-[13px] leading-[18px] text-ink-muted">{days.note}</p>
              </div>
            </div>
            {empty ? (
              <ChartMessage slots={slots} period={period} today={today}>
                <span>За эти дни отметок нет</span>
                {period.preset !== DEFAULT_DAYS && (
                  <TextButton onClick={() => onPeriod({ period: DEFAULT_DAYS })}>
                    Показать 2 недели
                  </TextButton>
                )}
              </ChartMessage>
            ) : (
              <MoodChart
                period={period}
                slots={slots}
                today={today}
                onOpenEntry={onOpenEntry}
                onShowWeek={onShowWeek}
              />
            )}
            {summary && <p className="text-[13px] leading-[18px] text-ink-muted">{summary}</p>}
          </div>
        )}
      </div>
    </Section>
  );
}

/** График без точек с сообщением по центру поля: пустой период или ошибка загрузки. */
function ChartMessage({
  slots,
  period,
  today,
  children,
}: {
  slots: Slot[];
  period: Period;
  today: Day;
  children: ReactNode;
}) {
  return (
    <div className="relative">
      <MoodChart
        period={period}
        slots={slots.map((s) => ({ ...s, marks: [], value: null, markedDays: 0 }))}
        today={today}
        onOpenEntry={() => {}}
        onShowWeek={null}
      />
      <div
        className="absolute flex items-center justify-center"
        style={{ left: CHART.left, width: CHART.plot, top: 0, height: CHART.bottom + 8 }}
      >
        <p
          role="status"
          className="flex flex-wrap items-center justify-center gap-x-2 gap-y-1 bg-surface px-4 py-1 text-sm text-ink-muted"
        >
          {children}
        </p>
      </div>
    </div>
  );
}

function TextButton({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="text-[15px] font-semibold text-primary-text hover:text-primary-hover focus-visible:outline-2 focus-visible:outline-focus"
    >
      {children}
    </button>
  );
}

/** Скелетон итога и графика (доска состояний, «Загрузка»). */
function ChartSkeleton() {
  const bar = "rounded bg-surface-sunken motion-safe:animate-pulse";
  return (
    <div aria-hidden className="flex flex-col gap-4">
      <div className="flex items-end justify-between">
        <div className="flex flex-col gap-2">
          <span className={`h-3 w-24 ${bar}`} />
          <span className={`h-6 w-32 ${bar}`} />
        </div>
        <div className="flex flex-col items-end gap-2">
          <span className={`h-3 w-24 ${bar}`} />
          <span className={`h-3 w-36 ${bar}`} />
        </div>
      </div>
      <div className="flex flex-col gap-6" style={{ width: CHART.width, height: CHART.height }}>
        {[0, 1, 2, 3, 4].map((i) => (
          <div key={i} className="flex items-center gap-4">
            <span className={`h-2.5 w-12 ${bar}`} />
            <span className="h-px flex-1 bg-line" />
          </div>
        ))}
      </div>
      <span className={`h-3 w-80 ${bar}`} />
    </div>
  );
}
