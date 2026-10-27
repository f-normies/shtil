import { fetchClient, getSessionGeneration, isSessionExpiryPending } from "@compass/api-client";
import type { QueryClient } from "@tanstack/react-query";
import { useRouter } from "@tanstack/react-router";
import { useCallback, useEffect, useRef, useState } from "react";
import type { PatientSession } from "../session";
import { getAssessmentStore, ME_KEY } from "../session";
import {
  isOfflineAssessmentAccessDenied,
  observeOfflineAssessmentAccessDenial,
} from "./offline-access";
import { subscribeToOfflineAccessDenials, subscribeToOwnerChanges } from "./owner-events";
import {
  type AssessmentSyncOutcome,
  assessmentSyncAdapter,
  createAssessmentSyncEngine,
} from "./sync";

type SyncEngine = ReturnType<typeof createAssessmentSyncEngine>;
const engines = new WeakMap<QueryClient, SyncEngine>();

async function runAssessmentSync(
  queryClient: QueryClient,
  expectedOwnerId: string,
  sessionGeneration: number,
): Promise<AssessmentSyncOutcome> {
  const store = await getAssessmentStore();
  let engine = engines.get(queryClient);
  if (!engine) {
    engine = createAssessmentSyncEngine(store, assessmentSyncAdapter);
    engines.set(queryClient, engine);
  }
  return engine.sync(expectedOwnerId, sessionGeneration);
}

/** Verify the route owner before refreshing any account-scoped assessment query. */
export function useAssessmentSynchronization(
  session: PatientSession,
  queryClient: QueryClient,
  routeKey = "",
): {
  syncNow: () => Promise<AssessmentSyncOutcome | null>;
  outcome: AssessmentSyncOutcome | null;
  sessionRevalidating: boolean;
} {
  const router = useRouter();
  const [outcome, setOutcome] = useState<AssessmentSyncOutcome | null>(null);
  const [sessionRevalidating, setSessionRevalidating] = useState(session.status === "offline");
  const revalidationInProgress = useRef(false);
  const revalidationRequested = useRef(false);
  const ownerId = session.status === "online" ? session.user.id : null;
  const routeOwnerId = ownerId ?? (session.status === "offline" ? session.ownerId : null);

  const revalidateRouteSession = useCallback(async () => {
    if (revalidationInProgress.current) {
      revalidationRequested.current = true;
      return;
    }
    revalidationInProgress.current = true;
    setSessionRevalidating(true);
    try {
      const cancellation = queryClient.cancelQueries();
      queryClient.clear();
      await cancellation;
      await router.invalidate();
    } catch {
      await router.navigate({ to: "/login" });
    } finally {
      revalidationInProgress.current = false;
      setSessionRevalidating(false);
      if (revalidationRequested.current) {
        revalidationRequested.current = false;
        queueMicrotask(() => void revalidateRouteSession());
      }
    }
  }, [queryClient, router]);

  const syncNow = useCallback(async () => {
    if (!ownerId || session.status !== "online") return null;
    const sessionGeneration = getSessionGeneration(fetchClient);
    const result = await runAssessmentSync(queryClient, ownerId, sessionGeneration);
    if (
      result.status === "session-changed" ||
      result.sessionGeneration !== getSessionGeneration(fetchClient)
    ) {
      return null;
    }
    setOutcome(result);
    if (result.status === "authentication-required") {
      if (isSessionExpiryPending(fetchClient)) return result;
      queryClient.setQueryData(ME_KEY, null);
      await router.navigate({ to: "/login", replace: true });
      const cancellation = queryClient.cancelQueries();
      queryClient.clear();
      await cancellation;
      return result;
    }
    const pointer = await (await getAssessmentStore()).getOwnerPointer();
    const pointerMatchesRoute = pointer?.id === ownerId && !pointer.locked;
    if (result.status !== "complete" || !pointerMatchesRoute || routeOwnerId !== ownerId) {
      await queryClient.cancelQueries();
      queryClient.clear();
      await router.invalidate();
      return result.status === "complete"
        ? { ...result, status: "owner-mismatch" as const }
        : result;
    }
    await queryClient.invalidateQueries({ queryKey: ["assessment-local", ownerId] });
    if (result.confirmedSubmissionIds.length > 0) {
      await queryClient.invalidateQueries({ queryKey: ["assessment-history", ownerId] });
    }
    return result;
  }, [ownerId, queryClient, routeOwnerId, router, session.status]);

  useEffect(() => {
    const syncOnReturn = () => {
      if (document.visibilityState === "visible") void revalidateRouteSession();
    };
    const unsubscribeOwnerChanges = subscribeToOwnerChanges(() => {
      void revalidateRouteSession();
    });
    const unsubscribeOfflineAccessDenials = subscribeToOfflineAccessDenials(() => {
      observeOfflineAssessmentAccessDenial();
      void revalidateRouteSession();
    });
    if (session.status === "offline") {
      if (isOfflineAssessmentAccessDenied()) {
        observeOfflineAssessmentAccessDenial();
        void revalidateRouteSession();
      } else {
        setSessionRevalidating(false);
      }
    } else if (ownerId && !sessionRevalidating && routeKey) {
      void syncNow();
    }
    window.addEventListener("online", syncOnReturn);
    document.addEventListener("visibilitychange", syncOnReturn);
    return () => {
      unsubscribeOwnerChanges();
      unsubscribeOfflineAccessDenials();
      window.removeEventListener("online", syncOnReturn);
      document.removeEventListener("visibilitychange", syncOnReturn);
    };
  }, [ownerId, revalidateRouteSession, routeKey, session.status, sessionRevalidating, syncNow]);

  return { syncNow, outcome, sessionRevalidating };
}
