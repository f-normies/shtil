import { fetchClient } from "@compass/api-client";
import { useQuery } from "@tanstack/react-query";
import {
  FALLBACK_HELP,
  type HelpContent,
  type PatientHelp,
  readStoredHelp,
  storeHelp,
} from "./cache";

export const PATIENT_HELP_KEY = ["patient-help"] as const;

async function fetchPatientHelp(): Promise<PatientHelp> {
  const { data, response } = await fetchClient.GET("/api/v1/patient-help");
  if (!data) throw new Error(`Данные экрана помощи не получены: код ${response.status}.`);
  storeHelp(data);
  return data;
}

/**
 * Данные экрана помощи (спека #57, раздел 6): ответ сервера, без сети — сохранённый ответ,
 * без него — запасной вариант с номером 112. Экран никогда не остаётся без номера.
 */
export function usePatientHelp(): { content: HelpContent; help: PatientHelp | null } {
  const query = useQuery({
    queryKey: PATIENT_HELP_KEY,
    queryFn: fetchPatientHelp,
    initialData: readStoredHelp() ?? undefined,
    // Сохранённый ответ показываем сразу, но обновляем при каждом открытии экрана.
    initialDataUpdatedAt: 0,
    staleTime: 0,
    retry: false,
  });
  const help = query.data ?? null;
  return { content: help ?? FALLBACK_HELP, help };
}
