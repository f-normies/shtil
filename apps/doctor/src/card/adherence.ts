/**
 * Раздел «Приём» (спека #34, раздел 6; кадр «Врач v4 · 07»): доля доз со знаменателем и
 * периодом, пропуски, схема, календарь доз и записи пациента о приёме. Дни — по московскому
 * календарю, как считает сервер. Чистые функции, без React.
 */
import type { Schemas } from "@compass/api-client";
import { plural } from "./documents";
import { FINDING } from "./proposals";
import { timesText } from "./therapy";

type Adherence = Schemas["AdherenceOut"];
type Course = Adherence["prescriptions"][number];
type Day = Adherence["days"][number];
type Period = NonNullable<Adherence["period"]>;
type Proposal = Schemas["ProposalOut"];
export type DoseStatus = Day["doses"][number]["status"];
export type Sex = string | null | undefined;

const DAY_MONTH = new Intl.DateTimeFormat("ru-RU", {
  day: "numeric",
  month: "long",
  timeZone: "UTC",
});
const MOSCOW_DAY = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Moscow" });
const MOSCOW_TIME = new Intl.DateTimeFormat("ru-RU", {
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Europe/Moscow",
});
const WEEKDAY = new Intl.DateTimeFormat("ru-RU", { weekday: "short", timeZone: "UTC" });

/** Прилагательное приёма для «2 вечерние дозы»; у остальных подписей — дата с подписью. */
const ADJECTIVES: Record<string, readonly [string, string, string]> = {
  Утро: ["утренняя", "утренние", "утренних"],
  День: ["дневная", "дневные", "дневных"],
  Вечер: ["вечерняя", "вечерние", "вечерних"],
};
const DOSES = ["доза", "дозы", "доз"] as const;

const utcDay = (iso: string) => new Date(`${iso}T00:00:00Z`);

/** «9.10» — день без нуля, как в строке «Пропущено» кадра 07. */
const shortDay = (iso: string) => {
  const d = utcDay(iso);
  return `${d.getUTCDate()}.${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
};

/** «05.10» — как в строке «Схема» кадра 07. */
const paddedDay = (iso: string) => `${iso.slice(8, 10)}.${iso.slice(5, 7)}`;

/** Процент принятых доз; без отметок доли нет. */
export function percentOf(a: Adherence): number | null {
  if (!a.marked_any || a.planned === 0) {
    return null;
  }
  return Math.round((a.taken / a.planned) * 100);
}

/** «26 из 28 доз»: после «из» — родительный падеж. */
export function dosesText(taken: number, planned: number): string {
  return `${taken} из ${planned} ${plural(planned, ["дозы", "доз", "доз"])}`;
}

/** «Приём за 14 дней, 5–18 октября». */
export function periodTitle(period: Period): string {
  const start = utcDay(period.start);
  const end = utcDay(period.end);
  const range =
    start.getUTCMonth() === end.getUTCMonth()
      ? `${start.getUTCDate()}–${DAY_MONTH.format(end)}`
      : `${DAY_MONTH.format(start)} – ${DAY_MONTH.format(end)}`;
  return `Приём за 14 дней, ${range}`;
}

export const patientGenitive = (sex: Sex) => (sex === "female" ? "пациентки" : "пациента");

/** Источник рядом с числом: «за 14 дней, по отметкам пациентки». */
export function shareNote(sex: Sex): string {
  return `за 14 дней, по отметкам ${patientGenitive(sex)}`;
}

/** Подпись приёма дозы по назначению. */
export function slotLabel(a: Adherence, prescriptionId: string, slot: number): string {
  const course = a.prescriptions.find((p) => p.id === prescriptionId);
  return course?.slot_labels[slot] ?? `Приём ${slot + 1}`;
}

/** «2 вечерние дозы: 9.10 и 14.10»; разные приёмы — «3 дозы: 5.10 утро, 9.10 вечер, …». */
export function missedText(a: Adherence): string | null {
  const missed = a.days.flatMap((d) =>
    d.doses
      .filter((x) => x.status === "missed")
      .map((x) => ({ day: d.day, label: slotLabel(a, x.prescription_id, x.slot) })),
  );
  if (missed.length === 0) {
    return null;
  }
  const n = missed.length;
  const labels = new Set(missed.map((m) => m.label));
  const [only] = labels;
  const adjective = labels.size === 1 && only ? ADJECTIVES[only] : undefined;
  if (adjective) {
    const days = missed.map((m) => shortDay(m.day));
    const list = days.length > 1 ? `${days.slice(0, -1).join(", ")} и ${days.at(-1)}` : days[0];
    return `${n} ${plural(n, adjective)} ${plural(n, DOSES)}: ${list}`;
  }
  const list = missed.map((m) => `${shortDay(m.day)} ${m.label.toLowerCase()}`).join(", ");
  return `${n} ${plural(n, DOSES)}: ${list}`;
}

/** «Авиандр 20 мг, 2 раза в сутки, утром и вечером, с 05.10». */
export function regimenLine(p: Course): string {
  return (
    `${p.trade_name} ${p.dose_mg} мг, ${timesText(p.times_per_day)} в сутки, ` +
    `${p.schedule}, с ${paddedDay(p.started_on)}`
  );
}

/** Пометка у схемы, когда действующего назначения нет. */
export function courseNote(a: Adherence): string | null {
  if (a.course === "completed") {
    return "курс завершён";
  }
  if (a.course === "stopped") {
    const stopped = a.prescriptions.map((p) => p.stopped_on).filter((d): d is string => !!d);
    const last = stopped.sort().at(-1);
    return last ? `назначение отменено ${paddedDay(last)}` : "назначение отменено";
  }
  return null;
}

/** Строка «Приём» в «Состоянии»: «26 из 28 доз за 14 дней», с пометкой о конце курса. */
export function indicatorText(a: Adherence): string {
  if (percentOf(a) === null) {
    return "нет отметок";
  }
  const note = courseNote(a);
  const share = `${dosesText(a.taken, a.planned)} за 14 дней`;
  return note ? `${share}, ${note}` : share;
}

/** Дни по неделям с понедельника: между неделями в календаре промежуток. */
export function weeksOf(days: readonly Day[]): Day[][] {
  const weeks: Day[][] = [];
  for (const d of days) {
    const monday = utcDay(d.day).getUTCDay() === 1;
    const current = weeks.at(-1);
    if (!current || monday) {
      weeks.push([d]);
    } else {
      current.push(d);
    }
  }
  return weeks;
}

export type Lane = { prescriptionId: string; slot: number; label: string; title: string | null };

/** Дорожки календаря: приёмы каждого назначения; при нескольких — с названием препарата. */
export function lanesOf(a: Adherence): Lane[] {
  const many = a.prescriptions.length > 1;
  return a.prescriptions.flatMap((p) =>
    p.slot_labels.map((label, slot) => ({
      prescriptionId: p.id,
      slot,
      label,
      title: many && slot === 0 ? regimenLine(p) : null,
    })),
  );
}

const STATUS_WORDS: Record<string, string> = { taken: "принята", missed: "пропущена" };

/** «14 октября, вечер, пропущена, по отметке пациентки» — ячейка для экранной читалки. */
export function cellLabel(day: string, label: string, status: DoseStatus, sex: Sex): string {
  const when = `${DAY_MONTH.format(utcDay(day))}, ${label.toLowerCase()}`;
  return status
    ? `${when}, ${STATUS_WORDS[status]}, по отметке ${patientGenitive(sex)}`
    : `${when}, нет отметки`;
}

/** Время записи пациента: `recorded_at` находки, как в очереди #96; без него — время предложения. */
export function recordedAt(p: Proposal): string {
  const at = p.payload.recorded_at;
  return typeof at === "string" && at ? at : p.created_at;
}

/** Записи пациента о приёме: находки `adherence` периода; ждущие решения выше, новые выше. */
export function adherenceFindings(proposals: readonly Proposal[], period: Period): Proposal[] {
  return proposals
    .filter(
      (p) =>
        p.kind === FINDING &&
        p.payload.category === "adherence" &&
        (p.status === "proposed" || p.status === "confirmed"),
    )
    .filter((p) => {
      const day = MOSCOW_DAY.format(new Date(recordedAt(p)));
      return period.start <= day && day <= period.end;
    })
    .sort(
      (a, b) =>
        Number(b.status === "proposed") - Number(a.status === "proposed") ||
        recordedAt(b).localeCompare(recordedAt(a)),
    );
}

/** Время отметки для поповера; отметка позже дня дозы — с датой. */
export function markedText(day: string, markedAt: string): string {
  const at = new Date(markedAt);
  const markedDay = MOSCOW_DAY.format(at);
  const time = MOSCOW_TIME.format(at);
  return markedDay === day ? `отмечено в ${time}` : `отмечено ${shortDay(markedDay)} в ${time}`;
}

/** «пн» — день недели в шапке календаря. */
export function weekdayShort(day: string): string {
  return WEEKDAY.format(utcDay(day)).replace(".", "");
}
