import { SegmentedControl } from "@compass/ui";
import { useEffect, useRef, useState } from "react";
import { type Day, shortDate } from "../../card/days";
import { isValidRange, type Period, type PeriodSearch, PRESETS } from "../../card/mood";
import { PeriodCalendar } from "./PeriodCalendar";

const CUSTOM = "custom";

/**
 * «Врач v4/Переключатель периода»: пять равных сегментов, выбор сразу применяет период.
 * «Период…» открывает календарь; выбранный в нём период подписан датами «дд.мм – дд.мм».
 */
export function PeriodSwitch({
  period,
  today,
  observationStart,
  onChange,
}: {
  period: Period;
  today: Day;
  observationStart: Day;
  onChange: (search: PeriodSearch) => void;
}) {
  const [open, setOpen] = useState(false);
  const wrapper = useRef<HTMLDivElement>(null);
  const custom = period.preset === null;
  const options = [
    ...PRESETS.map((p) => ({ value: String(p.days), label: p.label })),
    {
      value: CUSTOM,
      label: custom && !open ? `${shortDate(period.start)} – ${shortDate(period.end)}` : "Период…",
    },
  ];
  const valid = isValidRange(period.start, period.end, today, observationStart);

  // Щелчок мимо календаря закрывает его без изменений, как «Отмена».
  useEffect(() => {
    if (!open) {
      return;
    }
    function onPointerDown(event: PointerEvent) {
      if (event.target instanceof Node && !wrapper.current?.contains(event.target)) {
        setOpen(false);
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  function close() {
    setOpen(false);
    wrapper.current?.querySelector<HTMLInputElement>(`input[value="${CUSTOM}"]`)?.focus();
  }

  return (
    <div ref={wrapper} className="relative" data-forma="vrach">
      <SegmentedControl
        ariaLabel="Период графика настроения"
        className="w-full max-w-none"
        options={options}
        value={open || custom ? CUSTOM : String(period.preset)}
        onValueChange={(value) => {
          if (value === CUSTOM) {
            setOpen(true);
            return;
          }
          setOpen(false);
          onChange({ period: Number(value) });
        }}
        // Повторный щелчок по уже выбранному «Периоду…» тоже открывает календарь.
        onClick={(event) => {
          if (event.target instanceof HTMLInputElement && event.target.value === CUSTOM) {
            setOpen(true);
          }
        }}
      />
      {open && (
        <div className="absolute top-full right-0 z-20 mt-2">
          <PeriodCalendar
            limits={{ today, observationStart }}
            initial={valid ? { start: period.start, end: period.end } : { start: null, end: null }}
            onApply={(range) => {
              close();
              onChange(range);
            }}
            onCancel={close}
          />
        </div>
      )}
    </div>
  );
}
