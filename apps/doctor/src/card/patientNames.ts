/**
 * ФИО пациентов для плашки и списка сигналов. Своих пациентов даёт `GET /patients`.
 * Пациента, чей сигнал передан по замещению (спека #56), там нет: его ФИО — из
 * `GET /patients/{id}`, ключ запроса общий с `usePatient`.
 */
import { $api, type Schemas } from "@compass/api-client";
import { useQueries } from "@tanstack/react-query";
import { usePatients } from "./queries";

type Alert = Schemas["AlertOut"];

export function usePatientNames(alerts: readonly Alert[]): Map<string, string> {
  const patients = usePatients();
  const handed = [...new Set(alerts.filter((a) => a.as_deputy).map((a) => a.patient_id))];
  const deputies = useQueries({
    queries: handed.map((id) => ({
      ...$api.queryOptions("get", "/api/v1/patients/{patient_id}", {
        params: { path: { patient_id: id } },
      }),
      retry: false,
    })),
  });
  const found = [...(patients.data ?? []), ...deputies.flatMap((q) => (q.data ? [q.data] : []))];
  return new Map(found.map((p) => [p.id, p.full_name]));
}
