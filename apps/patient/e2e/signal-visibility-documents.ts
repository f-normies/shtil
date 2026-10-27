import type { Schemas } from "@compass/api-client";
import type { BrowserContext } from "@playwright/test";
import { DOCTOR, LEBEDEVA } from "./doctor-window-mock";

/**
 * «Документы» карточки для проверки #36 (кадры 07б и 07в): факты из выписки ждут решения,
 * один подтверждён, один отклонён. Выписка и факты выдуманы.
 */

const DOCUMENT: Schemas["DocumentOut"] = {
  id: "doc-1",
  patient_id: LEBEDEVA,
  uploaded_by: DOCTOR.id,
  filename: "Выписка терапевта.pdf",
  content_type: "application/pdf",
  size: 1024,
  sha256: "0".repeat(64),
  pages: 2,
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
};

const BASE = { document_id: "doc-1", quote: "со слов пациентки", page: 1, ocr: false };

function fact(
  id: string,
  kind: string,
  payload: Record<string, unknown>,
  status: Schemas["ProposalOut"]["status"] = "proposed",
): Schemas["ProposalOut"] {
  const decided = status !== "proposed";
  return {
    id,
    patient_id: LEBEDEVA,
    kind,
    payload: { ...BASE, ...payload },
    origin_type: "ai_run",
    origin_ref: "run",
    status,
    created_at: "2026-10-18T08:04:00Z",
    decided_by: decided ? DOCTOR.id : null,
    decided_at: decided ? "2026-10-18T09:00:00Z" : null,
    decision_comment: status === "rejected" ? "в выписке ошибка" : null,
  };
}

const FACTS: Schemas["ProposalOut"][] = [
  fact("dx", "ingestion.diagnosis", {
    icd10: "G47.0",
    text: "Нарушения засыпания и поддержания сна",
    code_in_text: false,
  }),
  fact("med", "ingestion.medication", {
    name: "Мелатонин",
    name_in_text: "мелатонин",
    renamed: false,
    dose: "3 мг",
    regimen: "на ночь",
    status: "current",
    stopped_on: null,
    stopped_on_source: null,
    document_date: "2026-09-12",
    drug: { name: "Мелатонин", check: "registered", matches: [] },
  }),
  fact("risk", "ingestion.fact", { field: "risk_factor", fact: "Курит 10 лет" }),
  fact("ok", "ingestion.fact", { field: "anamnesis", fact: "Тревога с весны" }, "confirmed"),
  fact("no", "ingestion.fact", { field: "anamnesis", fact: "Гипертония" }, "rejected"),
];

/** Подменить документы и предложения пациентки поверх `openWindow`. */
export async function documentsWithFacts(context: BrowserContext) {
  const base = `/api/v1/patients/${LEBEDEVA}`;
  await context.route(`**${base}/documents`, (route) => route.fulfill({ json: [DOCUMENT] }));
  await context.route(`**${base}/proposals`, (route) => route.fulfill({ json: FACTS }));
}
