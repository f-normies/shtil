/**
 * Запросы карточки пациента к API. Серверные данные — только TanStack Query через
 * сгенерированный клиент (CLAUDE.md).
 */
import { $api, fetchClient, type Schemas } from "@compass/api-client";
import {
  keepPreviousData,
  type QueryClient,
  useInfiniteQuery,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useState } from "react";
import { pollInterval as documentsPollInterval, finishedDocuments } from "./documents";
import { isSlow, nextPollStart, pollInterval } from "./guidance";
import { startOfToday } from "./proposalQueue";
import { type SourceRef, sourceQuery } from "./sources";

export const OPEN_ALERTS = "/api/v1/alerts";
export const PATIENT_ALERTS = "/api/v1/patients/{patient_id}/alerts";
export const PATIENT_PROPOSALS = "/api/v1/patients/{patient_id}/proposals";
export const PATIENT_ASSESSMENTS = "/api/v1/patients/{patient_id}/assessments";
export const PATIENT_SCHEDULE = "/api/v1/patients/{patient_id}/assessments/schedule";
export const PATIENT_DIAGNOSES = "/api/v1/patients/{patient_id}/diagnoses";
export const PATIENT_PRESCRIPTIONS = "/api/v1/patients/{patient_id}/prescriptions";
export const PATIENT_MEDICATIONS = "/api/v1/patients/{patient_id}/medications";
export const PATIENT_REACTIONS = "/api/v1/patients/{patient_id}/reactions";
export const ADVERSE_EVENT_DRAFT = "/api/v1/prescriptions/{prescription_id}/adverse-event-draft";
export const PATIENT_DOCUMENTS = "/api/v1/patients/{patient_id}/documents";
export const PATIENT_GUIDANCE = "/api/v1/patients/{patient_id}/therapy/guidance";
export const PATIENT_ADHERENCE = "/api/v1/patients/{patient_id}/adherence";
export const QUEUE = "/api/v1/proposals";
export const QUEUE_SUMMARY = "/api/v1/proposals/summary";
export const PATIENT_SUMMARY = "/api/v1/patients/{patient_id}/summary";
const PATIENTS = "/api/v1/patients";
const PATIENT = "/api/v1/patients/{patient_id}";
const UNREAD = "/api/v1/messaging/unread";
const PAGE = 100;
const LATEST = 10;
const QUEUE_PAGE = 50;

const pathOf = (patientId: string) => ({ params: { path: { patient_id: patientId } } });

export function usePatient(patientId: string) {
  return $api.useQuery("get", PATIENT, pathOf(patientId), { retry: false });
}

export function usePatients() {
  return $api.useQuery("get", PATIENTS);
}

/**
 * Предложения всех пациентов врача: общей очереди нет (#96), кабинет собирает её сам
 * (спека #13, раздел 10.2). Ключи — как у `usePatientProposals`, кэш общий.
 */
export function useProposalsOf(patientIds: readonly string[]) {
  const results = useQueries({
    queries: patientIds.map((id) => $api.queryOptions("get", PATIENT_PROPOSALS, pathOf(id))),
  });
  return {
    proposals: results.flatMap((r) => r.data ?? []),
    pending: results.some((r) => r.isPending),
  };
}

/** Последние результаты шкал каждого пациента — для второй строки списка «Пациенты». */
export function useLatestAssessments(patientIds: readonly string[]) {
  const results = useQueries({
    queries: patientIds.map((id) =>
      $api.queryOptions("get", PATIENT_ASSESSMENTS, {
        params: { path: { patient_id: id }, query: { limit: LATEST } },
      }),
    ),
  });
  return new Map(patientIds.map((id, i) => [id, results[i]?.data?.items ?? []]));
}

/** Приём за 14 дней по отметкам пациента (спека #34, раздел 5). */
export function usePatientAdherence(patientId: string, enabled = true) {
  return $api.useQuery("get", PATIENT_ADHERENCE, pathOf(patientId), { enabled });
}

/** Плановые даты шкал пациента со статусами (#111): из них пропуски на графике. */
export function useScaleSchedule(patientId: string) {
  return $api.useQuery("get", PATIENT_SCHEDULE, pathOf(patientId));
}

/** Шкалы, которые заполняет врач (#94). Каталог меняется только с релизом. */
export function useClinicianScales() {
  return $api.useQuery(
    "get",
    "/api/v1/assessments/clinician-scales",
    {},
    { staleTime: Number.POSITIVE_INFINITY },
  );
}

/** Непрочитанные сообщения пациентов врача (#33). */
export function useUnread() {
  return $api.useQuery("get", UNREAD);
}

export function usePatientAlerts(patientId: string) {
  return $api.useQuery("get", PATIENT_ALERTS, pathOf(patientId));
}

export function useOpenAlerts() {
  return $api.useQuery("get", OPEN_ALERTS, { params: { query: { status: "open" } } });
}

/** Сигналы экрана «Сигналы»: открытые или решённые, по всем пациентам или одному (спека #95). */
export function useAlerts(status: "open" | "resolved", patientId?: string) {
  return $api.useQuery("get", OPEN_ALERTS, {
    params: { query: { status, patient_id: patientId } },
  });
}

/** Варианты решений и отсрочка. Данные меняются только с релизом: кэш бессрочный. */
export function useAlertDecisions() {
  return $api.useQuery(
    "get",
    "/api/v1/alerts/decisions",
    {},
    { staleTime: Number.POSITIVE_INFINITY },
  );
}

/** Пороги правил по шкале для блока «Причина». */
export function useThresholds(scale: string | undefined) {
  return $api.useQuery(
    "get",
    "/api/v1/alerts/thresholds",
    { params: { query: { scale: scale ?? "" } } },
    { enabled: scale !== undefined, staleTime: Number.POSITIVE_INFINITY },
  );
}

/** Предложения пациента. Список обновляется и при возврате на вкладку браузера (спека #96). */
export function usePatientProposals(patientId: string) {
  return $api.useQuery("get", PATIENT_PROPOSALS, pathOf(patientId), {
    refetchOnWindowFocus: "always",
  });
}

/**
 * Очередь врача (спека #96, 3.1): предложения всех его пациентов по статусу, страницы
 * по 50 через `next_cursor`, поиск по ФИО. При возврате на вкладку браузера список
 * перечитывается. Пока ответ по новому поиску не пришёл, виден прежний список того же
 * статуса; другой статус начинается с загрузки.
 * Ключ — как у `$api.useQuery`, поэтому `invalidatePaths` его находит.
 */
export function useProposalQueue(status: Schemas["ProposalOut"]["status"], q: string) {
  const query = { status, q: q || undefined };
  const queryKey = ["get", QUEUE, { params: { query } }] as const;
  return useInfiniteQuery({
    queryKey,
    queryFn: async ({ pageParam, signal }) => {
      const { data, error } = await fetchClient.GET(QUEUE, {
        params: { query: { ...query, limit: QUEUE_PAGE, cursor: pageParam } },
        signal,
      });
      if (error) {
        throw error;
      }
      return data;
    },
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    placeholderData: (previous, previousQuery) =>
      previousQuery?.queryKey[2].params.query.status === status
        ? keepPreviousData(previous)
        : undefined,
    refetchOnWindowFocus: "always",
  });
}

/**
 * Сколько ждут решения и сколько решено с начала сегодняшнего дня по поясу браузера
 * (спека #96, 3.4): полоса «Разобрано сегодня» и счётчик в меню.
 */
export function useProposalSummary() {
  return $api.useQuery(
    "get",
    QUEUE_SUMMARY,
    { params: { query: { since: startOfToday(new Date()) } } },
    { refetchOnWindowFocus: "always" },
  );
}

export function useDiagnoses(patientId: string) {
  return $api.useQuery("get", PATIENT_DIAGNOSES, pathOf(patientId));
}

export function useMedications(patientId: string) {
  return $api.useQuery("get", PATIENT_MEDICATIONS, pathOf(patientId));
}

export function usePrescriptions(patientId: string) {
  return $api.useQuery("get", PATIENT_PRESCRIPTIONS, pathOf(patientId));
}

/** Отметки нежелательных реакций пациентом (спека #29). */
export function useReactionReports(patientId: string) {
  return $api.useQuery("get", PATIENT_REACTIONS, pathOf(patientId));
}

/** Черновик извещения запрашивается, только когда врач его открыл. */
export function useAdverseEventDraft(prescriptionId: string, enabled: boolean) {
  return $api.useQuery(
    "get",
    ADVERSE_EVENT_DRAFT,
    { params: { path: { prescription_id: prescriptionId } } },
    { enabled, retry: false },
  );
}

/**
 * Справка по КР в «Терапии» (спека #107, решение 11). Опрос раз в 5 с, пока статус
 * `pending`, и не дольше 3 минут. `slow` — опрос остановлен, а справка ещё считается.
 * `refresh` запрашивает справку снова и даёт опросу ещё 3 минуты.
 */
export function useGuidance(patientId: string) {
  const [since, setSince] = useState<number | null>(null);
  const query = $api.useQuery("get", PATIENT_GUIDANCE, pathOf(patientId), {
    refetchInterval: (q) => pollInterval(q.state.data?.status, since, q.state.dataUpdatedAt),
  });
  const status = query.data?.status;
  // Ответ из кэша до первого запроса после открытия панели опрос не начинает: он мог прийти
  // давно, и опрос остановился бы сразу.
  const next = nextPollStart(since, status, query.isFetchedAfterMount ? query.dataUpdatedAt : null);
  if (next !== since) {
    // Состояние по прошлому ответу: React перерисует компонент сразу, без эффекта.
    setSince(next);
  }
  const refresh = () => {
    setSince(Date.now());
    void query.refetch();
  };
  return { query, slow: isSlow(status, next, query.dataUpdatedAt), refresh };
}

/**
 * Документ и текст раздела по ссылке основания или фрагмент утверждения (спека #106).
 * Манифест и индекс меняются только с релизом и переиндексацией: кэш бессрочный.
 */
export function useSourceView(ref: SourceRef) {
  return $api.useQuery(
    "get",
    "/api/v1/knowledge/sources/{source_id}",
    { params: { path: { source_id: ref.source }, query: sourceQuery(ref) } },
    { retry: false, staleTime: Number.POSITIVE_INFINITY },
  );
}

/** Вся история шкал пациента: страницы по 100, курсор `before` (спека #13, раздел 3). */
export function useAssessmentHistory(patientId: string) {
  return useQuery({
    queryKey: ["get", PATIENT_ASSESSMENTS, pathOf(patientId), "all"],
    queryFn: async ({ signal }) => {
      const items: Schemas["AssessmentOut"][] = [];
      let before: string | undefined;
      do {
        const { data, error } = await fetchClient.GET(PATIENT_ASSESSMENTS, {
          params: { path: { patient_id: patientId }, query: { limit: PAGE, before } },
          signal,
        });
        if (error) {
          throw error;
        }
        items.push(...data.items);
        before = data.next_cursor ?? undefined;
      } while (before);
      return items;
    },
  });
}

/**
 * Истории болезни пациента, новые первыми (спека #30, раздел 4). Пока документ в работе,
 * список опрашивается раз в 5 с. Документ, дошедший до «Готово», приносит предложения:
 * их список обновляется тут же, иначе факты появятся только через `staleTime`.
 * Ключ — как у `$api.useQuery`, поэтому `invalidatePaths` его находит.
 */
export function useDocuments(patientId: string) {
  const queryClient = useQueryClient();
  const queryKey = ["get", PATIENT_DOCUMENTS, pathOf(patientId)];
  return useQuery({
    queryKey,
    queryFn: async ({ signal }) => {
      const { data, error } = await fetchClient.GET(PATIENT_DOCUMENTS, {
        ...pathOf(patientId),
        signal,
      });
      if (error) {
        throw error;
      }
      const before = queryClient.getQueryData<Schemas["DocumentOut"][]>(queryKey);
      if (finishedDocuments(before, data)) {
        void refreshProposals(queryClient);
      }
      return data;
    },
    refetchInterval: (query) => documentsPollInterval(query.state.data),
  });
}

/**
 * Тело загрузки: multipart с одним полем `file`. В схеме OpenAPI поле — строка (`binary`),
 * а openapi-fetch отправляет `FormData` как есть, заголовок с границей ставит браузер.
 */
export function documentForm(file: File): { file: string } {
  const form = new FormData();
  form.append("file", file);
  return form as unknown as { file: string };
}

/** Определения шкал. Недоступная шкала (404) — `undefined`: график без зон, ответы без текста. */
export function useScaleDefinitions(scaleIds: readonly string[]) {
  const states = useScaleDefinitionStates(scaleIds);
  return new Map(scaleIds.map((id) => [id, states.get(id)?.data]));
}

/** Определения шкал с состоянием запроса: раздел «Шкалы» показывает ошибку словами (кадр 09в). */
export function useScaleDefinitionStates(scaleIds: readonly string[]) {
  const results = useQueries({
    queries: scaleIds.map((scaleId) =>
      $api.queryOptions(
        "get",
        "/api/v1/assessments/scales/{scale_id}",
        { params: { path: { scale_id: scaleId } } },
        { retry: false, staleTime: Number.POSITIVE_INFINITY },
      ),
    ),
  });
  return new Map(scaleIds.map((id, i) => [id, results[i]]));
}

function invalidatePaths(queryClient: QueryClient, paths: readonly string[]): Promise<void> {
  return queryClient.invalidateQueries({
    predicate: (query) => paths.includes(String(query.queryKey[1])),
  });
}

/**
 * События несут только id сигнала: обновляем сигналы всех пациентов (спека #13, раздел 3).
 * Отметка реакции пациентом приходит сигналом, поэтому обновляем отметки и черновик
 * извещения (спека #29).
 */
export function refreshAlerts(queryClient: QueryClient): Promise<void> {
  return invalidatePaths(queryClient, [
    OPEN_ALERTS,
    PATIENT_ALERTS,
    PATIENT_REACTIONS,
    ADVERSE_EVENT_DRAFT,
    PATIENT_SUMMARY,
  ]);
}

/** Новый ответ может закрыть пропуск (#111, правило 4): расписание обновляется вместе с историей. */
export function refreshAssessments(queryClient: QueryClient): Promise<void> {
  return invalidatePaths(queryClient, [PATIENT_ASSESSMENTS, PATIENT_SCHEDULE, PATIENT_SUMMARY]);
}

/** Новое сообщение, отметка прочтения или восстановление связи обновляют очередь. */
export function refreshUnread(queryClient: QueryClient): Promise<void> {
  return invalidatePaths(queryClient, [UNREAD]);
}

/** Предложения пациента, очередь врача и счётчик «Предложения» в меню (спека #96). */
export function refreshProposals(queryClient: QueryClient): Promise<void> {
  return invalidatePaths(queryClient, [PATIENT_PROPOSALS, QUEUE, QUEUE_SUMMARY, PATIENT_SUMMARY]);
}

export function refreshDocuments(queryClient: QueryClient): Promise<void> {
  return invalidatePaths(queryClient, [PATIENT_DOCUMENTS]);
}

/**
 * Сводка с прошлого приёма (спека #38). Её же обновляют `refreshAlerts`, `refreshAssessments`,
 * `refreshProposals` и `refreshTherapy`: решения врача и события realtime меняют факты
 * периода, и без нового запроса «Подтвердить сводку» получил бы 409 `stale`.
 */
export function refreshSummary(queryClient: QueryClient): Promise<void> {
  return invalidatePaths(queryClient, [PATIENT_SUMMARY]);
}

export function refreshGuidance(queryClient: QueryClient): Promise<void> {
  return invalidatePaths(queryClient, [PATIENT_GUIDANCE]);
}

/** После изменения профиля, диагнозов или назначений: открытые предложения терапии
 * сервер отклоняет, поэтому обновляем и их (спека #28, раздел 3). Справка по КР
 * сразу показывает «считается» или «устарело» (спека #107, раздел 6). */
export function refreshTherapy(queryClient: QueryClient): Promise<void> {
  return invalidatePaths(queryClient, [
    PATIENT,
    PATIENTS,
    PATIENT_DIAGNOSES,
    PATIENT_PRESCRIPTIONS,
    PATIENT_PROPOSALS,
    QUEUE,
    QUEUE_SUMMARY,
    PATIENT_MEDICATIONS,
    PATIENT_ALERTS,
    PATIENT_GUIDANCE,
    PATIENT_REACTIONS,
    ADVERSE_EVENT_DRAFT,
    PATIENT_SUMMARY,
  ]);
}
