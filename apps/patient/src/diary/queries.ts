import { fetchClient, getSessionGeneration, type Schemas } from "@compass/api-client";
import type { QueryClient, UseQueryResult } from "@tanstack/react-query";
import { useQuery } from "@tanstack/react-query";
import { getAssessmentStore, type PatientSession } from "../session";
import {
  fetchDiaryContext,
  fetchDiaryPrescriptions,
  fetchDiarySchedule,
  fetchIntakeMarks,
  fetchLatestDiaryMessage,
} from "./api";
import type { DiaryStore } from "./store";
import { DiaryStore as DiaryStoreClass } from "./store";
import type { DiaryContextCacheRecord, DiaryScheduleSnapshot, IntakeView } from "./types";

let diaryStorePromise: Promise<DiaryStore> | undefined;

/** Share the IndexedDB wrapper for local cache queries and background synchronization. */
export function getDiaryStore(): Promise<DiaryStore> {
  diaryStorePromise ??= DiaryStoreClass.open().catch((error: unknown) => {
    diaryStorePromise = undefined;
    throw error;
  });
  return diaryStorePromise;
}

/** Load today's explicit dose marks with one shared Query result shape. */
export async function loadIntakeView(
  ownerId: string,
  scheduledOn: string,
  online: boolean,
): Promise<IntakeView> {
  const store = await getDiaryStore();
  const cached = await store.getCachedIntakeMarks(ownerId, scheduledOn);
  if (!online || !navigator.onLine) {
    return { items: cached, source: cached.length ? "cache" : "empty" };
  }
  try {
    const response = await fetchIntakeMarks(scheduledOn);
    await store.cacheIntakeMarks(ownerId, scheduledOn, response.items);
    return { items: response.items, source: "server" };
  } catch {
    return { items: cached, source: cached.length ? "cache" : "unavailable" };
  }
}

export const diaryQueryKeys = {
  localContext: (ownerId: string) => ["diary", ownerId, "local-context"] as const,
  context: (ownerId: string) => ["diary", ownerId, "context"] as const,
  schedule: (ownerId: string) => ["diary", ownerId, "survey-schedule"] as const,
  prescriptions: (ownerId: string) => ["diary", ownerId, "prescriptions"] as const,
  latestMessage: (ownerId: string) => ["diary", ownerId, "latest-message"] as const,
};

type DiaryQueryState<T> = Pick<
  UseQueryResult<T, Error>,
  "data" | "isPending" | "isError" | "error"
>;

class DiarySessionChangedError extends Error {
  constructor() {
    super("The patient session changed while diary data was loading");
    this.name = "DiarySessionChangedError";
  }
}

async function requireCurrentOwner(ownerId: string, generation: number): Promise<void> {
  if (getSessionGeneration(fetchClient) !== generation) throw new DiarySessionChangedError();
  const pointer = await (await getAssessmentStore()).getOwnerPointer();
  if (
    getSessionGeneration(fetchClient) !== generation ||
    pointer?.id !== ownerId ||
    pointer.role !== "patient" ||
    pointer.locked
  ) {
    throw new DiarySessionChangedError();
  }
}

function requireOnlineContext(
  ownerId: string | null,
  generation: number | null,
): { ownerId: string; generation: number } {
  if (ownerId === null || generation === null) throw new DiarySessionChangedError();
  return { ownerId, generation };
}

async function fetchAndCache<T>(
  ownerId: string,
  generation: number,
  queryClient: QueryClient,
  request: () => Promise<T>,
  persist: (store: DiaryStore, ownerId: string, data: T) => Promise<DiaryContextCacheRecord>,
): Promise<T> {
  const store = await getDiaryStore();
  await requireCurrentOwner(ownerId, generation);
  const data = await request();
  await requireCurrentOwner(ownerId, generation);
  const cache = await persist(store, ownerId, data);
  queryClient.setQueryData(diaryQueryKeys.localContext(ownerId), cache);
  return data;
}

function onlineOwner(session: PatientSession): string | null {
  return session.status === "online" ? session.user.id : null;
}

/** Owner-scoped Query data for the shared patient shell and task4 routes. */
export function useDiaryOverviewQueries(
  session: PatientSession,
  queryClient: QueryClient,
): {
  cache: DiaryContextCacheRecord | null;
  context: DiaryQueryState<Schemas["DiaryContextOut"]>;
  schedule: DiaryQueryState<DiaryScheduleSnapshot>;
  prescriptions: DiaryQueryState<Schemas["ScheduleItem"][]>;
  latestMessage: DiaryQueryState<Schemas["MessageOut"] | null>;
} {
  const ownerId = onlineOwner(session) ?? (session.status === "offline" ? session.ownerId : null);
  const generation = session.status === "online" ? getSessionGeneration(fetchClient) : null;
  const localCache = useQuery({
    queryKey: diaryQueryKeys.localContext(ownerId ?? "unowned"),
    queryFn: async () => {
      if (!ownerId) return null;
      return (await getDiaryStore()).getContextCache(ownerId);
    },
    enabled: ownerId !== null,
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
    networkMode: "always",
  });
  const enabled = ownerId !== null && generation !== null;
  const options = {
    staleTime: 30_000,
    retry: false,
    refetchOnReconnect: true,
    refetchOnWindowFocus: true,
  } as const;
  const context = useQuery({
    queryKey: diaryQueryKeys.context(ownerId ?? "unowned"),
    queryFn: () => {
      const online = requireOnlineContext(ownerId, generation);
      return fetchAndCache(
        online.ownerId,
        online.generation,
        queryClient,
        fetchDiaryContext,
        (store, id, data) => store.cacheContext(id, data),
      );
    },
    enabled,
    ...options,
  });
  const schedule = useQuery({
    queryKey: diaryQueryKeys.schedule(ownerId ?? "unowned"),
    queryFn: () => {
      const online = requireOnlineContext(ownerId, generation);
      return fetchAndCache(
        online.ownerId,
        online.generation,
        queryClient,
        fetchDiarySchedule,
        (store, id, data) => store.cacheSchedule(id, data),
      );
    },
    enabled,
    ...options,
  });
  const prescriptions = useQuery({
    queryKey: diaryQueryKeys.prescriptions(ownerId ?? "unowned"),
    queryFn: () => {
      const online = requireOnlineContext(ownerId, generation);
      return fetchAndCache(
        online.ownerId,
        online.generation,
        queryClient,
        fetchDiaryPrescriptions,
        (store, id, data) => store.cachePrescriptions(id, data),
      );
    },
    enabled,
    ...options,
  });
  const latestMessage = useQuery({
    queryKey: diaryQueryKeys.latestMessage(ownerId ?? "unowned"),
    queryFn: () => {
      const online = requireOnlineContext(ownerId, generation);
      return fetchAndCache(
        online.ownerId,
        online.generation,
        queryClient,
        () => fetchLatestDiaryMessage(online.ownerId),
        (store, id, data) => store.cacheLatestMessage(id, data),
      );
    },
    enabled,
    ...options,
  });

  return {
    cache: localCache.data ?? null,
    context: pickQueryState(context),
    schedule: pickQueryState(schedule),
    prescriptions: pickQueryState(prescriptions),
    latestMessage: pickQueryState(latestMessage),
  };
}

function pickQueryState<T>(query: UseQueryResult<T, Error>): DiaryQueryState<T> {
  return {
    data: query.data,
    isPending: query.isPending,
    isError: query.isError,
    error: query.error,
  };
}
