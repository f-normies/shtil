/**
 * «Лекарства на сегодня» (спека #34, раздел 7; кадр «Пациент · 16»): подписи доз и отметки.
 * Время и «вечер» — по Москве, как дни приёма на сервере. Чистые функции, без React.
 */
import type { Schemas } from "@compass/api-client";

type Dose = Schemas["DoseOut"];
type Status = Dose["status"];
type Mark = Schemas["MarkIn"];

const WHEN: Record<string, string> = {
  Утро: "Утром",
  День: "Днём",
  Вечер: "Вечером",
};
const MOSCOW_TIME = new Intl.DateTimeFormat("ru-RU", {
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Europe/Moscow",
});
const MOSCOW_HOUR = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  hourCycle: "h23",
  timeZone: "Europe/Moscow",
});
// Вечер в кабинете пациента начинается в 17:00 (#31): до него вечерняя доза не пропущена.
const EVENING_HOUR = 17;
const MOSCOW_DAY = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Moscow" });

/** Календарный день приёма по Москве. */
export function moscowDay(now: Date): string {
  return MOSCOW_DAY.format(now);
}

/** «Утро» → «Утром»; «На ночь» и «Приём 2» остаются как есть. */
export function slotWhen(label: string): string {
  return WHEN[label] ?? label;
}

/** «Утром, принято в 08:40», «Вечером, пропущено» или «Вечером». */
export function doseLine(dose: Dose): string {
  const when = slotWhen(dose.slot_label);
  if (dose.status === "taken" && dose.marked_at) {
    return `${when}, принято в ${MOSCOW_TIME.format(new Date(dose.marked_at))}`;
  }
  if (dose.status === "missed") {
    return `${when}, пропущено`;
  }
  return when;
}

/** Касание пустого квадрата — «принято», отмеченного — снять отметку. */
export function nextStatus(status: Status): Status {
  return status === "taken" ? null : "taken";
}

export function isEvening(now: Date): boolean {
  return Number(MOSCOW_HOUR.format(now)) >= EVENING_HOUR;
}

/**
 * Пустые дозы дня как пропущенные — то, что отправляет «Готово». Приём, время которого
 * ещё не пришло (`due: false`, считает сервер), остаётся без отметки.
 */
export function closingMarks(day: string, doses: readonly Dose[]): Mark[] {
  return doses
    .filter((d) => d.status === null && d.due)
    .map((d) => ({ prescription_id: d.prescription_id, day, slot: d.slot, status: "missed" }));
}

/** Дозы дня с отметками, ещё не подтверждёнными сервером; при ошибке экран вернёт прежние. */
export function withMarks(
  day: Schemas["DayDosesOut"],
  marks: readonly Mark[],
  now: Date,
): Schemas["DayDosesOut"] {
  return {
    ...day,
    doses: day.doses.map((d) => {
      const m = marks.find(
        (x) => x.prescription_id === d.prescription_id && x.slot === d.slot && x.day === day.day,
      );
      if (!m) return d;
      const status = m.status ?? null;
      return { ...d, status, marked_at: status ? now.toISOString() : null };
    }),
  };
}

// Подписи приёмов по словам расписания — как `adherence/doses.slot_labels` на сервере.
const SCHEDULE_WORDS: [RegExp, string][] = [
  [/(^|[\s,])утр/, "Утром"],
  [/дн[её]м/, "Днём"],
  [/вечер/, "Вечером"],
  [/на ночь|перед сном/, "На ночь"],
];

type IntakePrescription = Pick<
  Schemas["ScheduleItem"],
  "prescription_id" | "trade_name" | "dose_mg" | "times_per_day" | "schedule"
>;
export type IntakeMark = { status: "taken" | "unmarked"; occurred_at: string | null };

/** «Утром», «Вечером» по расписанию, если слов столько же, сколько приёмов; иначе «Приём N». */
export function intakeSlotLabels(timesPerDay: number, schedule: string): string[] {
  const text = schedule.toLowerCase();
  const named = SCHEDULE_WORDS.filter(([pattern]) => pattern.test(text)).map(([, label]) => label);
  if (named.length === timesPerDay) return named;
  if (timesPerDay === 1) return ["Приём"];
  return Array.from({ length: timesPerDay }, (_, index) => `Приём ${index + 1}`);
}

/** Строка приёма в отметке и на главной (кадры 12, 16): «Утром, принято в 08:40». */
export function intakeLine(
  prescription: IntakePrescription,
  ordinal: number,
  mark: IntakeMark | undefined,
): string {
  const when =
    intakeSlotLabels(prescription.times_per_day, prescription.schedule)[ordinal - 1] ??
    `Приём ${ordinal}`;
  if (mark?.status === "taken" && mark.occurred_at) {
    return `${when}, принято в ${MOSCOW_TIME.format(new Date(mark.occurred_at))}`;
  }
  if (mark?.status === "taken") return `${when}, принято`;
  if (mark?.status === "unmarked") return `${when}, приём не отмечен`;
  return when;
}
