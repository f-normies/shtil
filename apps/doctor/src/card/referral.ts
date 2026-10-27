/**
 * Направление к психиатру (спека #35; кадры «Врач v4 · 11», «11б», «11в»): какие сигналы
 * показывает лист, что подсказать под кнопкой отказа. Чистые функции, без React.
 */
import type { Schemas } from "@compass/api-client";
import { byPriority } from "./alerts";

type Alert = Schemas["AlertOut"];
type Proposal = Schemas["ProposalOut"];

export const REFERRAL = "alerts.referral";

export function isReferral(proposal: Proposal): boolean {
  return proposal.kind === REFERRAL;
}

/** Нерешённые сигналы маршрутизации пациента: их закрывает одно решение по направлению. */
export function routingSignals(alerts: readonly Alert[], patientId: string): Alert[] {
  return alerts
    .filter((a) => a.patient_id === patientId && a.kind === "routing" && a.state !== "resolved")
    .sort(byPriority);
}

/** Сигналы «Обязательно», которых не было при открытии листа (кадр 11, пограничный случай). */
export function arrivedMandatory(seenIds: readonly string[], current: readonly Alert[]): Alert[] {
  return current.filter((a) => a.level === "mandatory" && !seenIds.includes(a.id));
}

/** Почему «Отклонить направление» пока нельзя нажать. Комментарий нужен всегда (#93). */
export function declineHint(reasonId: string | null, comment: string): string | null {
  if (reasonId === null) {
    return "Выберите причину";
  }
  return comment.trim() ? null : "Напишите комментарий";
}

/** Отложить можно, только если все сигналы — «Желательно» (#95: «Обязательно» не откладывается). */
export function canSnooze(alerts: readonly Alert[]): boolean {
  return alerts.length > 0 && alerts.every((a) => a.level === "desirable");
}
