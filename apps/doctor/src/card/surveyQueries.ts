/**
 * Запросы блока «Опросы пациента» (спека #180) через сгенерированный клиент. После
 * сохранения обновляются настройки и плановые даты: от них зависят пропуски на графике.
 */
import { $api } from "@compass/api-client";
import { useQueryClient } from "@tanstack/react-query";
import { PATIENT_SCHEDULE } from "./queries";

export const PATIENT_SCALE_SETTINGS = "/api/v1/patients/{patient_id}/assessments/settings";
const PATIENT_SCALE_SETTING = "/api/v1/patients/{patient_id}/assessments/settings/{scale_id}";
const REFRESHED: readonly string[] = [PATIENT_SCALE_SETTINGS, PATIENT_SCHEDULE];

/** Какие шкалы расписания пациент заполняет и как часто. */
export function useScaleSettings(patientId: string) {
  return $api.useQuery("get", PATIENT_SCALE_SETTINGS, {
    params: { path: { patient_id: patientId } },
  });
}

/** Сохранить выбор врача по одной шкале. */
export function useSaveScaleSetting() {
  const queryClient = useQueryClient();
  return $api.useMutation("put", PATIENT_SCALE_SETTING, {
    onSettled: () =>
      queryClient.invalidateQueries({
        predicate: (query) => REFRESHED.includes(String(query.queryKey[1])),
      }),
  });
}
