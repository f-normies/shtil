import { fetchClient, getSessionGeneration } from "@compass/api-client";
import type { QueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";
import type { AssessmentSyncOutcome } from "../assessments/sync";
import { getAssessmentStore, type PatientSession } from "../session";
import { getDiaryStore, useDiaryOverviewQueries } from "./queries";
import { createDiarySyncEngine, type DiarySyncOutcome } from "./sync";
import { diarySyncAdapter } from "./sync-api";

type Engine = ReturnType<typeof createDiarySyncEngine>;
const engines = new WeakMap<QueryClient, Engine>();

async function runDiarySync(
  queryClient: QueryClient,
  ownerId: string,
  generation: number,
): Promise<DiarySyncOutcome> {
  const diary = await getDiaryStore();
  const assessments = await getAssessmentStore();
  let engine = engines.get(queryClient);
  if (!engine) {
    engine = createDiarySyncEngine(diary, assessments, diarySyncAdapter);
    engines.set(queryClient, engine);
  }
  return engine.sync(ownerId, generation);
}

export type PatientSyncState = {
  assessmentSyncNow: () => Promise<AssessmentSyncOutcome | null>;
  diarySyncNow: () => Promise<DiarySyncOutcome | null>;
  assessmentOutcome: AssessmentSyncOutcome | null;
  diaryOutcome: DiarySyncOutcome | null;
  sessionRevalidating: boolean;
};

/** Start a diary outbox sync for each patient route and on reconnect or app return. */
export function useDiarySynchronization(
  session: PatientSession,
  queryClient: QueryClient,
  routeKey: string,
): {
  syncNow: () => Promise<DiarySyncOutcome | null>;
  outcome: DiarySyncOutcome | null;
  overview: ReturnType<typeof useDiaryOverviewQueries>;
} {
  const ownerId =
    session.status === "online"
      ? session.user.id
      : session.status === "offline"
        ? session.ownerId
        : null;
  const isOnline = session.status === "online";
  const sessionGeneration = isOnline ? getSessionGeneration(fetchClient) : null;
  const overview = useDiaryOverviewQueries(session, queryClient);
  const [outcome, setOutcome] = useState<DiarySyncOutcome | null>(null);

  const syncNow = useCallback(async () => {
    if (!ownerId || sessionGeneration === null || !isOnline) return null;
    const result = await runDiarySync(queryClient, ownerId, sessionGeneration);
    if (
      result.sessionGeneration !== getSessionGeneration(fetchClient) ||
      result.status === "session-changed"
    ) {
      return null;
    }
    setOutcome(result);
    return result;
  }, [isOnline, ownerId, queryClient, sessionGeneration]);

  useEffect(() => {
    if (!isOnline || !ownerId || !routeKey) return;
    void syncNow();
    const syncOnReturn = () => {
      if (document.visibilityState === "visible") void syncNow();
    };
    window.addEventListener("online", syncOnReturn);
    document.addEventListener("visibilitychange", syncOnReturn);
    return () => {
      window.removeEventListener("online", syncOnReturn);
      document.removeEventListener("visibilitychange", syncOnReturn);
    };
  }, [isOnline, ownerId, routeKey, syncNow]);

  return { syncNow, outcome, overview };
}
