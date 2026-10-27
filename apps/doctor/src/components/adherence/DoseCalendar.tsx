import type { Schemas } from "@compass/api-client";
import { type KeyboardEvent, useId, useRef, useState } from "react";
import {
  cellLabel,
  type DoseStatus,
  type Lane,
  lanesOf,
  markedText,
  patientGenitive,
  type Sex,
  weekdayShort,
  weeksOf,
} from "../../card/adherence";
import { Section } from "../signals/Section";

type Adherence = Schemas["AdherenceOut"];
type Dose = Adherence["days"][number]["doses"][number];
type Cell = { day: number; lane: number };

const DAY_MONTH = new Intl.DateTimeFormat("ru-RU", {
  day: "numeric",
  month: "long",
  timeZone: "UTC",
});
const STATUS_TITLE: Record<string, string> = { taken: "Принята", missed: "Пропущена" };

/**
 * «Дозы по дням» (кадр 07): дорожки приёмов по дням, недели разделены промежутком.
 * Принята — точка, пропущена — кольцо, нет отметки — чёрточка; дни без приёма пусты.
 * Tab ставит фокус в календарь, ←→ — по дням, ↑↓ — по дорожкам, Esc закрывает поповер.
 * Для экранных читалок — скрытая таблица доз по дням.
 */
export function DoseCalendar({ adherence, sex }: { adherence: Adherence; sex: Sex }) {
  const lanes = lanesOf(adherence);
  const days = adherence.days;
  const weeks = weeksOf(days);
  const [current, setCurrent] = useState<Cell>({ day: Math.max(days.length - 1, 0), lane: 0 });
  const [hovered, setHovered] = useState<Cell | null>(null);
  const [focused, setFocused] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const cells = useRef(new Map<string, HTMLButtonElement>());
  const tipId = useId();
  const key = (c: Cell) => `${c.day}:${c.lane}`;
  const doseAt = (c: Cell): Dose | undefined => {
    const lane = lanes[c.lane];
    return days[c.day]?.doses.find(
      (d) => d.prescription_id === lane?.prescriptionId && d.slot === lane?.slot,
    );
  };
  const shown = dismissed ? null : (hovered ?? (focused ? current : null));

  const move = (next: Cell) => {
    setCurrent(next);
    setDismissed(false);
    cells.current.get(key(next))?.focus();
  };
  const onKeyDown = (event: KeyboardEvent) => {
    const steps: Record<string, Cell> = {
      ArrowLeft: { day: Math.max(current.day - 1, 0), lane: current.lane },
      ArrowRight: { day: Math.min(current.day + 1, days.length - 1), lane: current.lane },
      ArrowUp: { day: current.day, lane: Math.max(current.lane - 1, 0) },
      ArrowDown: { day: current.day, lane: Math.min(current.lane + 1, lanes.length - 1) },
    };
    const next = steps[event.key];
    if (next) {
      event.preventDefault();
      move(next);
    } else if (event.key === "Escape") {
      setDismissed(true);
    }
  };

  const counts = { taken: adherence.taken, missed: adherence.missed, none: adherence.unmarked };
  let index = 0;

  return (
    <Section title="Дозы по дням">
      <div className="flex flex-col gap-4">
        <div className="flex">
          <div aria-hidden className="flex w-[72px] shrink-0 flex-col pt-10">
            {lanes.map((l) => (
              <LaneTitle key={`${l.prescriptionId}:${l.slot}`} lane={l} />
            ))}
          </div>
          {/* Сетка с клавиатурой; та же информация для читалок — в скрытой таблице ниже. */}
          <fieldset
            aria-label="Дозы по дням"
            className="m-0 flex min-w-0 gap-4 border-0 p-0"
            onKeyDown={onKeyDown}
            onFocus={() => setFocused(true)}
            onBlur={(e) => {
              if (!e.currentTarget.contains(e.relatedTarget)) {
                setFocused(false);
              }
            }}
            onMouseLeave={() => setHovered(null)}
          >
            {weeks.map((week) => (
              <div key={week[0]?.day} className="flex">
                {week.map((d) => {
                  const day = index++;
                  return (
                    <div key={d.day} className="flex w-[37px] flex-col items-center">
                      <div aria-hidden className="flex h-10 flex-col items-center text-xs">
                        <span className="text-ink-muted">{weekdayShort(d.day)}</span>
                        <span className="text-ink">{Number(d.day.slice(8, 10))}</span>
                      </div>
                      {lanes.map((lane, l) => {
                        const cell = { day, lane: l };
                        const dose = doseAt(cell);
                        const active = shown?.day === day && shown.lane === l;
                        return (
                          <div
                            key={`${lane.prescriptionId}:${lane.slot}`}
                            className="relative h-9 w-full"
                          >
                            <button
                              ref={(el) => {
                                if (el) cells.current.set(key(cell), el);
                                else cells.current.delete(key(cell));
                              }}
                              type="button"
                              tabIndex={current.day === day && current.lane === l ? 0 : -1}
                              aria-label={
                                dose
                                  ? cellLabel(d.day, lane.label, dose.status, sex)
                                  : `${DAY_MONTH.format(new Date(`${d.day}T00:00:00Z`))}, ${lane.label.toLowerCase()}, приёма нет`
                              }
                              aria-describedby={active ? tipId : undefined}
                              onClick={() => move(cell)}
                              onMouseEnter={() => {
                                setHovered(cell);
                                setDismissed(false);
                              }}
                              className="flex size-full items-center justify-center rounded outline-none focus-visible:ring-2 focus-visible:ring-primary"
                            >
                              {dose && (
                                <span aria-hidden className="absolute inset-x-0 h-px bg-line" />
                              )}
                              {dose && <Mark status={dose.status} />}
                            </button>
                            {active && dose && (
                              <Tip
                                id={tipId}
                                day={d.day}
                                label={lane.label}
                                dose={dose}
                                sex={sex}
                              />
                            )}
                          </div>
                        );
                      })}
                    </div>
                  );
                })}
              </div>
            ))}
          </fieldset>
        </div>
        <ul aria-hidden className="flex gap-6 text-[13px] text-ink-muted">
          <li className="flex items-center gap-2">
            <Mark status="taken" />
            Принята: {counts.taken}
          </li>
          <li className="flex items-center gap-2">
            <Mark status="missed" />
            Пропущена: {counts.missed}
          </li>
          <li className="flex items-center gap-2">
            <Mark status={null} />
            Нет отметки: {counts.none}
          </li>
        </ul>
        <DoseTable adherence={adherence} lanes={lanes} sex={sex} />
      </div>
    </Section>
  );
}

function LaneTitle({ lane }: { lane: Lane }) {
  return (
    <span className="flex h-9 flex-col justify-center text-[15px] leading-5 text-ink">
      {lane.title && <span className="truncate text-xs text-ink-muted">{lane.title}</span>}
      {lane.label}
    </span>
  );
}

/** Отметка дозы: форма различает статус и без цвета. */
function Mark({ status }: { status: DoseStatus }) {
  if (status === "taken") {
    return <span className="relative size-2.5 rounded-full bg-primary" />;
  }
  if (status === "missed") {
    return <span className="relative size-2.5 rounded-full border-2 border-ink bg-surface" />;
  }
  return <span className="relative h-0.5 w-2.5 bg-ink-muted" />;
}

/** Поповер дозы: дата, приём, статус, время отметки и источник. Появляется за 150 мс. */
function Tip({
  id,
  day,
  label,
  dose,
  sex,
}: {
  id: string;
  day: string;
  label: string;
  dose: Dose;
  sex: Sex;
}) {
  return (
    <div
      id={id}
      role="tooltip"
      className="pointer-events-none absolute bottom-full left-1/2 z-20 mb-1 w-max max-w-60 -translate-x-1/2 animate-fade-in rounded-lg bg-ink px-3 py-2 text-[13px] leading-[18px] text-surface shadow-md"
    >
      <p className="font-semibold">
        {DAY_MONTH.format(new Date(`${day}T00:00:00Z`))}, {label.toLowerCase()}
      </p>
      <p>
        {dose.status ? STATUS_TITLE[dose.status] : "Нет отметки"}
        {dose.marked_at && `, ${markedText(day, dose.marked_at)}`}
      </p>
      {dose.status && <p className="opacity-80">по отметке {patientGenitive(sex)}</p>}
    </div>
  );
}

/** Скрытая таблица доз по дням для экранных читалок (аннотация кадра 07). */
function DoseTable({ adherence, lanes, sex }: { adherence: Adherence; lanes: Lane[]; sex: Sex }) {
  return (
    <table className="sr-only">
      <caption>Дозы по дням, по отметкам {patientGenitive(sex)}</caption>
      <thead>
        <tr>
          <th scope="col">День</th>
          {lanes.map((l) => (
            <th key={`${l.prescriptionId}:${l.slot}`} scope="col">
              {l.title ? `${l.title}, ${l.label}` : l.label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {adherence.days.map((d) => (
          <tr key={d.day}>
            <th scope="row">{DAY_MONTH.format(new Date(`${d.day}T00:00:00Z`))}</th>
            {lanes.map((l) => {
              const dose = d.doses.find(
                (x) => x.prescription_id === l.prescriptionId && x.slot === l.slot,
              );
              const text = !dose
                ? "приёма нет"
                : dose.status
                  ? (STATUS_TITLE[dose.status] ?? "").toLowerCase()
                  : "нет отметки";
              return <td key={`${l.prescriptionId}:${l.slot}`}>{text}</td>;
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
