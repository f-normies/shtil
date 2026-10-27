/**
 * Эскалация «Обязательно» в кабинете врача (спека #56, раздел 6): подписи шагов в ходе
 * обработки, пометка в плашке, доступ заместителя. Имена врачей — из `actors` сигнала.
 * Падежи ФИО не склоняем: «передан заместителю: Громов А. Н.».
 */
import type { Schemas } from "@compass/api-client";
import type { Step } from "./alerts";
import { shortName } from "./names";

type Alert = Schemas["AlertOut"];
type Action = Schemas["ActionOut"];

const MOSCOW_TIME = new Intl.DateTimeFormat("ru-RU", {
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Europe/Moscow",
});

const CLOSED: Record<NonNullable<Schemas["AccessGrantOut"]["end_reason"]>, string> = {
  resolved: "по решению сигнала",
  lead_took: "ведущий врач занялся сигналом",
  revoked: "назначение отозвано",
};

function minutesSince(alert: Alert, at: string): number {
  if (!alert.urgent_since) {
    return 0;
  }
  return Math.round((Date.parse(at) - Date.parse(alert.urgent_since)) / 60_000);
}

function nameOf(names: Record<string, string>, id: string | null | undefined): string {
  const full = id ? names[id] : undefined;
  return full ? shortName(full) : "";
}

/** Шаг эскалации в ходе обработки: «Не взят 15 мин — повтор», «Передан заместителю: …». */
export function escalationTitle(a: Action, alert: Alert, names: Record<string, string>): string {
  if (a.step === 1) {
    return `Не взят ${minutesSince(alert, a.at)} мин — повтор`;
  }
  if (a.step === 2) {
    return a.recipient_id
      ? `Передан заместителю: ${nameOf(names, a.recipient_id)}`
      : "Заместитель не назначен";
  }
  return "Сообщено администратору";
}

/** «Доступ заместителя закрыт» — когда и почему; действует — null. */
export function accessClosedStep(alert: Alert, now: Date): Step | null {
  const access = alert.deputy_access;
  if (!access) {
    return null;
  }
  if (access.ended_at && access.end_reason) {
    return {
      title: "Доступ заместителя закрыт",
      detail: CLOSED[access.end_reason],
      at: access.ended_at,
      done: true,
    };
  }
  if (Date.parse(access.expires_at) <= now.getTime()) {
    return {
      title: "Доступ заместителя закрыт",
      detail: "срок 12 часов истёк",
      at: access.expires_at,
      done: true,
    };
  }
  return null;
}

/** Пометка у сигнала в плашке: после шага 1 — «не взят 15 мин», после шага 2 — кому передан. */
export function bannerNote(alert: Alert): string | null {
  const steps = (alert.actions ?? []).filter((a) => a.action === "escalate");
  const last = steps.at(-1);
  if (!last) {
    return null;
  }
  const handed = steps.find((a) => a.step === 2 && a.recipient_id);
  if (handed) {
    return `передан заместителю: ${nameOf(alert.actors ?? {}, handed.recipient_id)}`;
  }
  const first = steps.find((a) => a.step === 1);
  return first ? `не взят ${minutesSince(alert, first.at)} мин` : null;
}

/** Заместитель видит, чей это пациент: «Замещение, ведущий врач — Соколова И. П.». */
export function deputyNote(alert: Alert): string | null {
  if (!alert.as_deputy) {
    return null;
  }
  const lead = nameOf(alert.actors ?? {}, alert.doctor_id);
  return lead ? `Замещение, ведущий врач — ${lead}` : "Замещение";
}

/** Шапка карточки заместителя: «Доступ по замещению до 21:30»; доступа нет — null. */
export function deputyAccessUntil(alerts: readonly Alert[], now: Date): string | null {
  const ends = alerts
    .filter((a) => a.as_deputy && a.deputy_access && !a.deputy_access.ended_at)
    .map((a) => Date.parse(a.deputy_access?.expires_at ?? ""))
    .filter((t) => t > now.getTime());
  if (ends.length === 0) {
    return null;
  }
  return `Доступ по замещению до ${MOSCOW_TIME.format(new Date(Math.max(...ends)))}`;
}
