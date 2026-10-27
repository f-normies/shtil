import { Alert, Brand, Button } from "@compass/ui";
import type { QueryClient } from "@tanstack/react-query";
import {
  createRootRouteWithContext,
  Outlet,
  useRouter,
  useRouterState,
} from "@tanstack/react-router";
import type { ReactNode } from "react";
import { useAssessmentSynchronization } from "../assessments/synchronization";
import { useDiarySynchronization } from "../diary/synchronization";
import { PatientSyncProvider } from "../patient-sync-context";
import type { PatientSession } from "../session";

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  component: PatientRoot,
  errorComponent: RootError,
});

function PatientRoot() {
  const { queryClient } = Route.useRouteContext();
  const { matches, pathname } = useRouterState({
    select: (state) => ({ matches: state.matches, pathname: state.location.pathname }),
  });
  const session = findPatientSession(matches);
  return (
    <div className="min-h-dvh font-sans">
      {session ? (
        <PatientSyncShell session={session} queryClient={queryClient} routeKey={pathname}>
          <Outlet />
        </PatientSyncShell>
      ) : (
        <Outlet />
      )}
    </div>
  );
}

function PatientSyncShell({
  session,
  queryClient,
  routeKey,
  children,
}: {
  session: PatientSession;
  queryClient: QueryClient;
  routeKey: string;
  children: ReactNode;
}) {
  const assessment = useAssessmentSynchronization(session, queryClient, routeKey);
  const diary = useDiarySynchronization(session, queryClient, routeKey);
  return (
    <PatientSyncProvider
      value={{
        assessmentSyncNow: assessment.syncNow,
        diarySyncNow: diary.syncNow,
        assessmentOutcome: assessment.outcome,
        diaryOutcome: diary.outcome,
        sessionRevalidating: assessment.sessionRevalidating,
      }}
    >
      {children}
    </PatientSyncProvider>
  );
}

function findPatientSession(matches: readonly unknown[]): PatientSession | null {
  for (const candidate of [...matches].reverse()) {
    if (!candidate || typeof candidate !== "object" || !("context" in candidate)) continue;
    const context = (candidate as { context?: unknown }).context;
    if (!context || typeof context !== "object" || !("session" in context)) continue;
    const session = (context as { session?: unknown }).session;
    if (!session || typeof session !== "object" || !("status" in session)) continue;
    const value = session as Partial<PatientSession>;
    if (value.status === "online" && "user" in value && value.user?.role === "patient") {
      return value as PatientSession;
    }
    if (value.status === "offline" && "ownerId" in value && typeof value.ownerId === "string") {
      return value as PatientSession;
    }
    if (value.status === "network-unavailable" && "cause" in value) {
      return value as PatientSession;
    }
  }
  return null;
}

/** Russian fallback for any route error; local answers stay in IndexedDB. */
function RootError() {
  const router = useRouter();
  return (
    <main className="mx-auto flex min-h-dvh max-w-lg flex-col justify-center gap-4 px-4 py-8 font-sans">
      <Brand persona="patient" className="patient-auth-brand" />
      <h1 className="text-xl font-semibold">Не удалось открыть приложение</h1>
      <Alert tone="error">
        Страница не загрузилась. Ответы, сохранённые на этом устройстве, не удалены.
      </Alert>
      <Button className="self-start" onClick={() => void router.invalidate()}>
        Повторить
      </Button>
    </main>
  );
}
