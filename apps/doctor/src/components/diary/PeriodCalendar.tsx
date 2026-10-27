import { Button } from "@compass/ui";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { type KeyboardEvent, type ReactNode, useEffect, useRef, useState } from "react";
import {
  addMonths,
  canPage,
  dayState,
  focusInView,
  type Limits,
  type Month,
  monthOfDay,
  monthTitle,
  monthWeeks,
  moveDay,
  pickDay,
  type Selection,
  selectionText,
} from "../../card/calendar";
import { type Day, dayMonth, dayNumber, yearOf } from "../../card/days";

const WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];

/**
 * «Врач v4/Поповер календаря» (кадр 06г): два месяца, первый щелчок — начало, второй —
 * конец. «Показать» неактивна, пока конец не выбран; Esc и «Отмена» закрывают без изменений.
 * Клавиатура: стрелки — по дням, PageUp и PageDown — по месяцам, Enter — выбрать.
 */
export function PeriodCalendar({
  limits,
  initial,
  onApply,
  onCancel,
}: {
  limits: Limits;
  initial: Selection;
  onApply: (range: { from: Day; to: Day }) => void;
  onCancel: () => void;
}) {
  const [selection, setSelection] = useState<Selection>(initial);
  const [focused, setFocused] = useState<Day>(initial.end ?? initial.start ?? limits.today);
  const [right, setRight] = useState<Month>(monthOfDay(focused));
  const left = addMonths(right, -1);
  const buttons = useRef(new Map<Day, HTMLButtonElement>());
  // Листание кнопками переносит только «свой» день сетки (tabIndex 0), фокус остаётся на кнопке.
  const moveFocus = useRef(true);

  // Фокус идёт за выбранным днём: при открытии — на конец периода, дальше — по клавишам.
  useEffect(() => {
    if (moveFocus.current) {
      buttons.current.get(focused)?.focus();
    }
  }, [focused]);

  function page(direction: -1 | 1) {
    const next = addMonths(right, direction);
    moveFocus.current = false;
    setRight(next);
    setFocused(focusInView(focused, next, limits));
  }

  function focusDay(day: Day) {
    moveFocus.current = true;
    const month = monthOfDay(day);
    if (month > right) {
      setRight(month);
    } else if (month < left) {
      setRight(addMonths(month, 1));
    }
    setFocused(day);
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      onCancel();
      return;
    }
    // Стрелки листают дни, только когда фокус в сетке: у кнопок внизу своё поведение.
    if (!(event.target instanceof HTMLElement) || !event.target.closest("table")) {
      return;
    }
    const next = moveDay(focused, event.key, limits);
    if (next !== focused) {
      event.preventDefault();
      focusDay(next);
    }
  }

  return (
    <div
      role="dialog"
      aria-label="Выбор периода"
      className="w-[568px] rounded-lg bg-surface p-5 shadow-[0_12px_32px_-4px_rgb(16_26_32/12%),0_2px_6px_rgb(16_26_32/8%)] motion-safe:animate-rise-in motion-reduce:animate-fade-in"
      onKeyDown={onKeyDown}
    >
      <p className="text-[15px] leading-5 font-semibold text-ink">
        Выберите начало и конец периода
      </p>
      <p className="text-[13px] leading-[18px] text-ink-muted">
        От 7 до 90 дней, с {dayMonth(limits.observationStart)} — начала наблюдения — по сегодня
      </p>
      <div className="mt-4 grid grid-cols-2 gap-6">
        {[left, right].map((month, i) => (
          <MonthGrid
            key={month}
            month={month}
            back={i === 0 ? () => page(-1) : null}
            forward={i === 1 ? () => page(1) : null}
            canBack={canPage(left, -1, limits)}
            canForward={canPage(right, 1, limits)}
            selection={selection}
            limits={limits}
            focused={focused}
            buttons={buttons.current}
            onPick={(day) => {
              setFocused(day);
              setSelection((s) => pickDay(s, day, limits));
            }}
          />
        ))}
      </div>
      <div className="mt-4 flex items-center gap-3">
        <p aria-live="polite" className="flex-1 text-sm text-ink">
          {selectionText(selection)}
        </p>
        <Button variant="secondary" onClick={onCancel}>
          Отмена
        </Button>
        <Button
          disabled={!selection.start || !selection.end}
          onClick={() => {
            if (selection.start && selection.end) {
              onApply({ from: selection.start, to: selection.end });
            }
          }}
        >
          Показать
        </Button>
      </div>
    </div>
  );
}

function MonthGrid({
  month,
  back,
  forward,
  canBack,
  canForward,
  selection,
  limits,
  focused,
  buttons,
  onPick,
}: {
  month: Month;
  back: (() => void) | null;
  forward: (() => void) | null;
  canBack: boolean;
  canForward: boolean;
  selection: Selection;
  limits: Limits;
  focused: Day;
  buttons: Map<Day, HTMLButtonElement>;
  onPick: (day: Day) => void;
}) {
  return (
    <div>
      <div className="flex h-8 items-center justify-between">
        <PageButton label="Предыдущий месяц" onClick={back} enabled={canBack}>
          <ChevronLeft aria-hidden className="size-4" />
        </PageButton>
        <p className="text-[15px] font-semibold text-ink">{monthTitle(month)}</p>
        <PageButton label="Следующий месяц" onClick={forward} enabled={canForward}>
          <ChevronRight aria-hidden className="size-4" />
        </PageButton>
      </div>
      <table className="mt-1 w-full border-collapse text-center">
        <thead>
          <tr>
            {WEEKDAYS.map((w) => (
              <th key={w} scope="col" className="h-6 text-xs font-normal text-ink-muted">
                {w}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {monthWeeks(month).map((week) => (
            <tr key={week.find(Boolean) ?? month}>
              {week.map((day, i) =>
                day ? (
                  <DayCell
                    key={day}
                    day={day}
                    selection={selection}
                    limits={limits}
                    focused={day === focused}
                    buttons={buttons}
                    onPick={onPick}
                  />
                ) : (
                  // biome-ignore lint/suspicious/noArrayIndexKey: пустые ячейки сдвига не меняются местами
                  <td key={i} />
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** «…/День»: круг 32 в ячейке 36; диапазон — полоса primary-soft, концы — круг primary. */
function DayCell({
  day,
  selection,
  limits,
  focused,
  buttons,
  onPick,
}: {
  day: Day;
  selection: Selection;
  limits: Limits;
  focused: boolean;
  buttons: Map<Day, HTMLButtonElement>;
  onPick: (day: Day) => void;
}) {
  const state = dayState(day, selection, limits);
  const edge = state.start || state.end;
  const band = state.inRange || (state.start && selection.end) || (state.end && selection.start);
  const half = state.start ? "left-1/2 right-0" : state.end ? "left-0 right-1/2" : "inset-x-0";
  const role = state.start ? ", начало периода" : state.end ? ", конец периода" : "";
  return (
    <td className="relative h-10 p-0">
      {band && <span aria-hidden className={`absolute inset-y-0.5 bg-primary-soft ${half}`} />}
      <button
        ref={(el) => {
          if (el) buttons.set(day, el);
          else buttons.delete(day);
        }}
        type="button"
        tabIndex={focused ? 0 : -1}
        aria-disabled={state.disabled || undefined}
        aria-pressed={edge}
        aria-label={`${dayMonth(day)} ${yearOf(day)}${role}${state.today ? ", сегодня" : ""}`}
        onClick={() => onPick(day)}
        className={[
          "relative mx-auto flex size-8 items-center justify-center rounded-full text-sm focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus",
          edge ? "bg-primary font-semibold text-primary-ink" : "",
          !edge && state.disabled ? "cursor-default text-ink-subtle/70" : "",
          !edge && !state.disabled ? "text-ink hover:bg-surface-sunken" : "",
          state.today && !edge ? "ring-[1.5px] ring-primary ring-inset" : "",
        ].join(" ")}
      >
        {dayNumber(day)}
      </button>
    </td>
  );
}

function PageButton({
  label,
  onClick,
  enabled,
  children,
}: {
  label: string;
  onClick: (() => void) | null;
  enabled: boolean;
  children: ReactNode;
}) {
  if (!onClick) {
    return <span className="size-8" />;
  }
  return (
    <button
      type="button"
      aria-label={label}
      disabled={!enabled}
      onClick={onClick}
      className="flex size-8 items-center justify-center rounded-md text-ink-muted hover:bg-surface-sunken disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-focus"
    >
      {children}
    </button>
  );
}
