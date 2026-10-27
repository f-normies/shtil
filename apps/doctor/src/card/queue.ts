/**
 * Очередь экрана «Пациенты» (спека #13, раздел 10.2; кадр «Врач v4 · 02»): группы
 * «Требуют внимания» и «Остальные», причина и время строки. Кабинет собирает очередь
 * из существующих ответов API, отдельной ручки нет. Чистые функции, без React.
 */
import type { Schemas } from "@compass/api-client";
import { consentRevokedShort } from "./consent";
import { plural } from "./documents";
import { shortScaleName } from "./levels";
import { needsDecision } from "./signals";

type Alert = Schemas["AlertOut"];
type Patient = Schemas["PatientSummary"];
type Proposal = Schemas["ProposalOut"];
type Unread = Schemas["UnreadOut"];
type Assessment = Schemas["AssessmentOut"];

/** Категория строки: от неё значок и место в очереди. */
export type QueueKind = "mandatory" | "desirable" | "proposal" | "unread" | "none";

export type QueueRow = {
  patient: Patient;
  kind: QueueKind;
  /** Вторая строка: причина внимания или последние баллы. */
  reason: string;
  /** Время события причины или последних данных. */
  at: string | null;
  /** Сигналы пациента, которые держат его в группе, самый важный первым. */
  alerts: Alert[];
  pending: Proposal[];
};

export type QueueInput = {
  patients: readonly Patient[];
  /** Открытые сигналы врача (`GET /alerts?status=open`). */
  alerts: readonly Alert[];
  /** Предложения пациентов врача, любые статусы. */
  proposals: readonly Proposal[];
  unread: readonly Unread[];
  /** Последние результаты шкал по пациентам, в любом порядке. */
  latest: ReadonlyMap<string, readonly Assessment[]>;
  now: Date;
};

const RANK: Record<QueueKind, number> = {
  mandatory: 0,
  desirable: 1,
  proposal: 2,
  unread: 3,
  none: 4,
};

/**
 * Пациент требует внимания, если у него есть сигнал «новый» или «в работе», ждущее
 * предложение или непрочитанное сообщение. Отложенный сигнал до даты возврата пациента
 * не держит: в срок сервер сам вернёт его в «новые» (спека #95).
 */
export function patientQueue(input: QueueInput): { attention: QueueRow[]; rest: QueueRow[] } {
  const rows = input.patients.map((p) => rowOf(p, input));
  const attention = rows
    .filter((r) => r.kind !== "none")
    .sort((a, b) => RANK[a.kind] - RANK[b.kind] || newer(a.at, b.at));
  const rest = rows.filter((r) => r.kind === "none").sort((a, b) => newer(a.at, b.at));
  return { attention, rest };
}

function rowOf(patient: Patient, input: QueueInput): QueueRow {
  const alerts = needsDecision(input.alerts.filter((a) => a.patient_id === patient.id));
  const pending = input.proposals
    .filter((p) => p.patient_id === patient.id && p.status === "proposed")
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
  const top = alerts[0];
  if (top) {
    const reason =
      alerts.length === 1
        ? top.title
        : `${alerts.length} ${plural(alerts.length, ["сигнал", "сигнала", "сигналов"])}, ${lowerFirst(top.title)}`;
    return { patient, kind: top.level, reason, at: top.last_triggered_at, alerts, pending };
  }
  if (pending.length > 0) {
    const n = pending.length;
    const reason = `${n} ${plural(n, ["предложение", "предложения", "предложений"])} ${plural(n, ["ждёт", "ждут", "ждут"])} решения`;
    return {
      patient,
      kind: "proposal",
      reason,
      at: pending[0]?.created_at ?? null,
      alerts,
      pending,
    };
  }
  const messages = input.unread.find((u) => u.patient_id === patient.id && u.unread_count > 0);
  if (messages) {
    const n = messages.unread_count;
    const reason = `${n} ${plural(n, ["новое сообщение", "новых сообщения", "новых сообщений"])}`;
    return { patient, kind: "unread", reason, at: messages.last_message_at, alerts, pending };
  }
  // Отзыв согласия (спека #112): новых данных не будет, вторая строка — отзыв и его дата.
  const revoked = consentRevokedShort(patient.consent_revoked_at);
  if (revoked) {
    return {
      patient,
      kind: "none",
      reason: revoked,
      at: patient.consent_revoked_at ?? null,
      alerts,
      pending,
    };
  }
  const latest = [...(input.latest.get(patient.id) ?? [])].sort((a, b) =>
    b.completed_at.localeCompare(a.completed_at),
  );
  return {
    patient,
    kind: "none",
    reason: latest.length > 0 ? scoresLine(latest) : "Данных пока нет",
    at: latest[0]?.completed_at ?? patient.created_at,
    alerts,
    pending,
  };
}

/** Последний балл каждой шкалы: «PHQ-9 7, GAD-7 8». Результаты — новые первыми. */
function scoresLine(results: readonly Assessment[]): string {
  const seen = new Map<string, string>();
  for (const r of results) {
    const score = Object.values(r.scores)[0];
    if (score && !seen.has(r.scale_id)) {
      seen.set(r.scale_id, `${shortScaleName(r.scale_title)} ${score.value}`);
    }
  }
  return [...seen.values()].join(", ");
}

function newer(a: string | null, b: string | null): number {
  return (b ?? "").localeCompare(a ?? "");
}

function lowerFirst(text: string): string {
  return text.charAt(0).toLowerCase() + text.slice(1);
}

/** Поиск по части ФИО без учёта регистра и «ё/е». Пустой запрос подходит всем. */
export function matchesName(fullName: string, query: string): boolean {
  const norm = (text: string) => text.trim().toLowerCase().replaceAll("ё", "е");
  return norm(fullName).includes(norm(query));
}
