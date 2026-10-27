/**
 * Тексты страницы администратора (спека #56, раздел 6): заместители и разбор эскалаций.
 * Данных пациента нет и в API: только номер сигнала, врачи и время. Время — по Москве.
 */
import type { Schemas } from "@compass/api-client";
import { shortName } from "../card/names";

type Escalation = Schemas["EscalationOut"];

const TIME = new Intl.DateTimeFormat("ru-RU", {
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Europe/Moscow",
});

const CLOSED: Record<NonNullable<Schemas["AccessGrantOut"]["end_reason"]>, string> = {
  resolved: "по решению сигнала",
  lead_took: "ведущий врач занялся сигналом",
  revoked: "назначение отозвано",
};

function time(iso: string): string {
  return TIME.format(new Date(iso));
}

function nameOf(e: Escalation, id: string | null | undefined): string {
  const full = id ? e.names[id] : undefined;
  return full ? shortName(full) : "";
}

/** «У 2 врачей нет заместителя на сегодня»; все с заместителем — null. */
export function noDeputyText(count: number): string | null {
  if (count === 0) {
    return null;
  }
  const one = count % 10 === 1 && count % 100 !== 11;
  return `У ${count} ${one ? "врача" : "врачей"} нет заместителя на сегодня`;
}

/** «20.10–26.10»: обе даты входят в период. */
export function periodText(starts: string, ends: string): string {
  const day = (iso: string) => `${iso.slice(8, 10)}.${iso.slice(5, 7)}`;
  return `${day(starts)}–${day(ends)}`;
}

/** Шаги эскалации по времени: «09:15 — повтор врачу». */
export function stepsText(e: Escalation): string[] {
  return e.steps.map((s) => {
    const what =
      s.step === 1
        ? "повтор врачу"
        : s.step === 2
          ? s.recipient_id
            ? `заместителю: ${nameOf(e, s.recipient_id)}`
            : "заместитель не назначен"
          : "сообщено администратору";
    return `${time(s.at)} — ${what}`;
  });
}

/** Доступ заместителя: «с 09:30 до 21:30», «с 09:30, закрыт в 09:50: …»; не выдавался — «—». */
export function accessText(e: Escalation, now: Date): string {
  const a = e.access;
  if (!a) {
    return "—";
  }
  if (a.ended_at && a.end_reason) {
    return `с ${time(a.granted_at)}, закрыт в ${time(a.ended_at)}: ${CLOSED[a.end_reason]}`;
  }
  if (Date.parse(a.expires_at) <= now.getTime()) {
    return `с ${time(a.granted_at)}, истёк в ${time(a.expires_at)}`;
  }
  return `с ${time(a.granted_at)} до ${time(a.expires_at)}`;
}

/** Плашка о шаге 3: «Срочный сигнал не взят 60 мин, ведущий врач — Соколова И. П.». */
export function bannerText(e: Escalation): string {
  const third = e.steps.find((s) => s.step === 3);
  const minutes =
    third && e.urgent_since
      ? Math.round((Date.parse(third.at) - Date.parse(e.urgent_since)) / 60_000)
      : null;
  const late = minutes === null ? "не взят в работу" : `не взят ${minutes} мин`;
  return `Срочный сигнал ${late}, ведущий врач — ${nameOf(e, e.doctor_id)}`;
}
