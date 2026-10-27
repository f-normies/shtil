/** Сведения о пациенте в шапке подробностей и карточки (спека #13, раздел 10.3). */
import type { Schemas } from "@compass/api-client";
import { formatDate } from "./format";
import { ageText, years } from "./therapy";

/**
 * «34 года, F41.1, Авиандр 20 мг, наблюдение с 22.09». Снятые диагнозы и назначения не входят,
 * повторы кодов схлопываются.
 * Год начала наблюдения — только если он не текущий (`today`).
 */
export function aboutLine({
  patient,
  diagnoses,
  prescriptions,
  today,
}: {
  patient: Schemas["PatientSummary"];
  diagnoses: readonly Schemas["DiagnosisOut"][];
  prescriptions: readonly Schemas["PrescriptionOut"][];
  today: string;
}): string {
  return [
    patient.birth_date ? ageText(years(patient.birth_date, today)) : null,
    // Один код может прийти несколькими записями (#191): в строке он один раз.
    ...new Set(diagnoses.filter((d) => d.resolved_at === null).map((d) => d.icd10)),
    ...prescriptions
      .filter((p) => p.status === "active")
      .map((p) => `${p.trade_name} ${p.dose_mg} мг`),
    `наблюдение с ${formatDate(patient.created_at, new Date(`${today}T12:00:00`))}`,
  ]
    .filter(Boolean)
    .join(", ");
}
