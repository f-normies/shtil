import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { getAssessmentStore, type PatientSession } from "../session";
import { getDiaryStore } from "./queries";

export function diaryLocalKeys(ownerId: string) {
  return {
    entries: ["diary", ownerId, "local-entries"] as const,
    drafts: ["diary", ownerId, "drafts"] as const,
    outbox: ["diary", ownerId, "outbox"] as const,
    definitions: ["diary", ownerId, "cached-definitions"] as const,
  };
}

export function diaryDraftsQueryOptions(ownerId: string | null) {
  return {
    queryKey: diaryLocalKeys(ownerId ?? "unowned").drafts,
    enabled: ownerId !== null,
    retry: false as const,
    networkMode: "always" as const,
    queryFn: async () => (ownerId ? (await getDiaryStore()).listDrafts(ownerId) : []),
  };
}

/** Read the real owner-scoped local records that feed the patient screens. */
export function useDiaryLocal(session: PatientSession) {
  const queryClient = useQueryClient();
  const ownerId =
    session.status === "online"
      ? session.user.id
      : session.status === "offline"
        ? session.ownerId
        : null;
  const keys = diaryLocalKeys(ownerId ?? "unowned");
  const entries = useQuery({
    queryKey: keys.entries,
    enabled: ownerId !== null,
    retry: false,
    networkMode: "always",
    queryFn: async () => (ownerId ? (await getDiaryStore()).listEntries(ownerId) : []),
  });
  const drafts = useQuery(diaryDraftsQueryOptions(ownerId));
  const outbox = useQuery({
    queryKey: keys.outbox,
    enabled: ownerId !== null,
    retry: false,
    networkMode: "always",
    queryFn: async () => (ownerId ? (await getDiaryStore()).getOutboxForOwner(ownerId) : []),
  });
  const definitions = useQuery({
    queryKey: keys.definitions,
    enabled: ownerId !== null,
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
    networkMode: "always",
    queryFn: async () => (ownerId ? (await getAssessmentStore()).getCachedDefinitions() : []),
  });

  const refresh = useCallback(async (): Promise<void> => {
    const refreshKeys = diaryLocalKeys(ownerId ?? "unowned");
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: refreshKeys.entries }),
      queryClient.invalidateQueries({ queryKey: refreshKeys.drafts }),
      queryClient.invalidateQueries({ queryKey: refreshKeys.outbox }),
    ]);
  }, [ownerId, queryClient]);

  return { ownerId, entries, drafts, outbox, definitions, refresh };
}
