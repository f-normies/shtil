/**
 * Запросы сводки с прошлого приёма (спека #38, раздел 3.6). Отдельно от `queries.ts`, чтобы
 * тот не вырос за 400 строк; путь и `refreshSummary` — там, рядом с общими обновлениями.
 */
import { $api, type Schemas } from "@compass/api-client";
import type { QueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { PATIENT_SUMMARY } from "./queries";
import { isSlow, nextPollStart, pollInterval } from "./summary";

const pathOf = (patientId: string) => ({ params: { path: { patient_id: patientId } } });

/**
 * Сводка с прошлого приёма в «Обзоре» (спека #38, раздел 3.6). Опрос раз в 3 с, пока
 * черновик считается, и не дольше 3 минут — как `useGuidance`. `slow` — опрос остановлен,
 * а черновик ещё считается. `refresh` запрашивает сводку снова и даёт опросу ещё 3 минуты.
 */
export function useVisitSummary(patientId: string) {
  const [since, setSince] = useState<number | null>(null);
  const query = $api.useQuery("get", PATIENT_SUMMARY, pathOf(patientId), {
    refetchInterval: (q) => pollInterval(q.state.data, since, q.state.dataUpdatedAt),
  });
  // Ответ из кэша до первого запроса после открытия «Обзора» опрос не начинает.
  const next = nextPollStart(
    since,
    query.data,
    query.isFetchedAfterMount ? query.dataUpdatedAt : null,
  );
  if (next !== since) {
    // Состояние по прошлому ответу: React перерисует компонент сразу, без эффекта.
    setSince(next);
  }
  const refresh = () => {
    setSince(Date.now());
    void query.refetch();
  };
  return { query, slow: isSlow(query.data, next, query.dataUpdatedAt), refresh };
}

/** Ответ подтверждения или повтора — та же сводка: кладём её в кэш без нового запроса. */
export function storeSummary(
  queryClient: QueryClient,
  patientId: string,
  summary: Schemas["VisitSummaryOut"],
): void {
  const { queryKey } = $api.queryOptions("get", PATIENT_SUMMARY, pathOf(patientId));
  queryClient.setQueryData(queryKey, summary);
}
