/** Выдуманные данные для тестов логики карточки. Реальных пациентов здесь нет. */
import type { Schemas } from "@compass/api-client";
import type { QueueItem } from "./proposalQueue";

type Alert = Schemas["AlertOut"];
type Trigger = Schemas["TriggerOut"];

export const PATIENT = "11111111-1111-1111-1111-111111111111";
export const OTHER_PATIENT = "22222222-2222-2222-2222-222222222222";
export const DOCTOR = "33333333-3333-3333-3333-333333333333";

export function trigger(overrides: Partial<Trigger> = {}): Trigger {
  return {
    at: "2026-10-18T08:12:00Z",
    evidence: {},
    level: "mandatory",
    origin_ref: "ref",
    rule_version: null,
    ...overrides,
  };
}

export function alert(overrides: Partial<Alert> = {}): Alert {
  return {
    id: "a1",
    patient_id: PATIENT,
    doctor_id: DOCTOR,
    origin_type: "rule",
    rule_id: "test-rule",
    rule_version: 1,
    kind: "alert",
    level: "mandatory",
    title: "Сигнал",
    doctor_actions: ["Связаться с пациентом"],
    sources: [],
    occurrences: 1,
    created_at: "2026-10-18T08:12:00Z",
    last_triggered_at: "2026-10-18T08:12:00Z",
    delivered_at: null,
    delivered_via: null,
    acknowledged_at: null,
    acknowledged_by: null,
    state: "new",
    snoozed_until: null,
    resolved_at: null,
    resolved_by: null,
    status: "created",
    triggers: [trigger()],
    actions: [],
    actors: {},
    decision: null,
    urgent_since: null,
    deputy_id: null,
    deputy_access: null,
    as_deputy: false,
    escalation_step: null,
    ...overrides,
  };
}

export function assessment(
  overrides: Partial<Schemas["AssessmentOut"]> = {},
): Schemas["AssessmentOut"] {
  return {
    id: "r1",
    submission_id: "s1",
    patient_id: PATIENT,
    scale_id: "test-mood",
    scale_version: 1,
    scale_title: "Тестовая шкала настроения",
    completed_at: "2026-10-01T09:00:00Z",
    received_at: "2026-10-01T09:00:05Z",
    answers: { q1: 1 },
    scores: {
      total: {
        value: 10,
        band: {
          code: "moderate",
          label: "Умеренная",
          source: {
            id: "src",
            locator: "с. 1",
            title: "Источник",
            url: "https://example.org",
            supplementary: false,
          },
        },
      },
    },
    sources: [],
    ...overrides,
  };
}

export function document(overrides: Partial<Schemas["DocumentOut"]> = {}): Schemas["DocumentOut"] {
  return {
    id: "d1",
    patient_id: PATIENT,
    uploaded_by: DOCTOR,
    filename: "Выписка терапевта.pdf",
    content_type: "application/pdf",
    size: 1024,
    sha256: "0".repeat(64),
    pages: 3,
    status: "done",
    error_code: null,
    error_page: null,
    page_sources: [],
    ocr: false,
    created_at: "2026-10-18T08:00:00Z",
    parsed_at: "2026-10-18T08:02:00Z",
    extracted_at: "2026-10-18T08:04:00Z",
    extract_run_id: "run",
    dropped: 0,
    document_date: "2026-09-12",
    known: [],
    parse_failures: 0,
    extract_failures: 0,
    ...overrides,
  };
}

export function proposal(overrides: Partial<Schemas["ProposalOut"]> = {}): Schemas["ProposalOut"] {
  return {
    id: "p1",
    patient_id: PATIENT,
    kind: "freetext.finding",
    payload: {},
    origin_type: "ai_run",
    origin_ref: "run",
    status: "proposed",
    created_at: "2026-10-18T08:13:00Z",
    decided_by: null,
    decided_at: null,
    decision_comment: null,
    ...overrides,
  };
}

export function patient(
  overrides: Partial<Schemas["PatientSummary"]> = {},
): Schemas["PatientSummary"] {
  return {
    id: PATIENT,
    full_name: "Тестова Анна Сергеевна",
    email: "test@example.org",
    phone: null,
    lead_doctor_id: DOCTOR,
    created_at: "2026-09-22T08:00:00Z",
    access: "lead",
    birth_date: null,
    sex: null,
    pregnant: null,
    breastfeeding: null,
    child_pugh: null,
    egfr: null,
    egfr_measured_on: null,
    ...overrides,
  };
}

export function unread(overrides: Partial<Schemas["UnreadOut"]> = {}): Schemas["UnreadOut"] {
  return {
    patient_id: PATIENT,
    doctor_id: DOCTOR,
    unread_count: 1,
    last_message_id: "m1",
    last_message_at: "2026-10-18T08:20:00Z",
    ...overrides,
  };
}

/** Предложение в очереди врача (спека #96). */
export function queueItem(overrides: Partial<QueueItem> = {}): QueueItem {
  return { ...proposal(), patient_name: "Лебедева Марина Викторовна", run: null, ...overrides };
}
