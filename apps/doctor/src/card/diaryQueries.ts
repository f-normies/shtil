/**
 * Запросы вкладки «Дневник» к ручкам #31 через сгенерированный клиент. Ключи начинаются как
 * у `$api.useQuery`, поэтому `invalidateQueries` по пути ручки находит и эти запросы.
 *
 * Сервер считает дни по своему часовому поясу (`settings.timezone`) и принимает 7–90 дней
 * от начала наблюдения до сегодня, `start` и `end` включительно (`periodQuery`).
 */
import { $api, fetchClient } from "@compass/api-client";
import { type QueryClient, useInfiniteQuery } from "@tanstack/react-query";
import type { Day } from "./days";

export const DIARY_MOODS = "/api/v1/patients/{patient_id}/diary/moods";
export const DIARY_ENTRIES = "/api/v1/patients/{patient_id}/diary";
const ENTRIES_PAGE = 20;

export type DiaryQuery = { start: Day; end: Day };

/** Все отметки настроения периода, без страниц. */
export function useDiaryMoods(patientId: string, query: DiaryQuery) {
  return $api.useQuery(
    "get",
    DIARY_MOODS,
    { params: { path: { patient_id: patientId }, query } },
    { select: (data) => data.items },
  );
}

/**
 * Записи периода, новые выше, страницами по 20: «Показать ещё записи» берёт следующую по
 * `next_cursor`. Фильтр сервера не используется: вкладка скрывает виды сама, и дни остаются.
 */
export function useDiaryEntries(patientId: string, query: DiaryQuery) {
  const path = { patient_id: patientId };
  return useInfiniteQuery({
    queryKey: ["get", DIARY_ENTRIES, { params: { path, query } }, "pages"] as const,
    queryFn: async ({ pageParam, signal }) => {
      const { data, error } = await fetchClient.GET(DIARY_ENTRIES, {
        params: {
          path,
          query: { ...query, filter: "all", cursor: pageParam, limit: ENTRIES_PAGE },
        },
        signal,
      });
      if (error) {
        throw error;
      }
      return data;
    },
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
}

/**
 * Событие `diary.changed` (новая запись, правка, скрытие, статус расшифровки): записи и
 * отметки пациента читаются заново. `null` — всех пациентов, после переподключения.
 */
export function refreshDiary(queryClient: QueryClient, patientId: string | null): Promise<void> {
  return queryClient.invalidateQueries({
    predicate: ({ queryKey }) => {
      if (queryKey[1] !== DIARY_ENTRIES && queryKey[1] !== DIARY_MOODS) {
        return false;
      }
      const init = queryKey[2] as { params?: { path?: { patient_id?: string } } } | undefined;
      return patientId === null || init?.params?.path?.patient_id === patientId;
    },
  });
}
