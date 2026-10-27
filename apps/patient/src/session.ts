import { fetchClient, fetchCurrentUser, type User } from "@compass/api-client";
import { confirmMessagingOwner, lockMessagingAccess } from "@compass/ui";
import { QueryClient } from "@tanstack/react-query";
import {
  clearOfflineAssessmentAccessDenial,
  denyOfflineAssessmentAccess,
  isOfflineAssessmentAccessDenied,
} from "./assessments/offline-access";
import { AssessmentStore } from "./assessments/store";
import { hasCachedPatientPreferences } from "./preferences/cache";

export const ME_KEY = ["me"] as const;
let nextSessionVerificationId = 0;
// The session check uses its own query client, so clearing app data after a 401 or owner
// change cannot cancel it. Assessment routes keep the default online pause; diary route
// guards opt into always so a saved, owner-scoped diary can still open without a network.
const verificationQueryClient = new QueryClient();

/** Why the session could not be checked: no network, or a 5xx or non-JSON reply from the server. */
export type SessionUnavailableCause = "network" | "server";

export type PatientSession =
  | { status: "online"; user: User }
  | { status: "offline"; ownerId: string; cause: SessionUnavailableCause }
  | { status: "other-role"; user: User }
  | { status: "unauthenticated" }
  | { status: "network-unavailable"; cause: SessionUnavailableCause };

let storePromise: Promise<AssessmentStore> | undefined;

/** Reuse the feature database connection across patient routes. */
export function getAssessmentStore(): Promise<AssessmentStore> {
  storePromise ??= AssessmentStore.open().catch((error: unknown) => {
    storePromise = undefined;
    throw error;
  });
  return storePromise;
}

class ServerUnavailableError extends Error {}

function unavailableCause(error: unknown): SessionUnavailableCause | null {
  if (error instanceof ServerUnavailableError) return "server";
  if (error instanceof TypeError) return "network";
  return null;
}

/** Read the signed-in user; null means there is no session. */
async function fetchSessionUser(): Promise<User | null> {
  const { data, response } = await fetchClient.GET("/api/v1/auth/me").catch((error: unknown) => {
    // openapi-fetch throws SyntaxError when a 2xx body is not JSON, for example a proxy page.
    if (error instanceof SyntaxError) throw new ServerUnavailableError("Non-JSON session reply");
    throw error;
  });
  if (response.status === 401) return null;
  if (data) return data;
  if (response.ok || response.status >= 500) {
    throw new ServerUnavailableError(`GET /api/v1/auth/me failed: ${response.status}`);
  }
  throw new Error(`GET /api/v1/auth/me failed: ${response.status}`);
}

/** Verify the session with the server before exposing account-scoped local data. */
export async function resolvePatientSession(
  queryClient: QueryClient,
  options: { allowOfflineDiary?: boolean } = {},
): Promise<PatientSession> {
  const store = await getAssessmentStore();
  const previousOwner = await store.getOwnerPointer();
  const verificationKey = [...ME_KEY, "verification", ++nextSessionVerificationId] as const;
  let user: User | null;
  try {
    user = await verificationQueryClient.fetchQuery({
      queryKey: verificationKey,
      queryFn: fetchSessionUser,
      networkMode: options.allowOfflineDiary ? "always" : undefined,
      staleTime: 0,
      retry: false,
    });
  } catch (error) {
    const cause = unavailableCause(error);
    if (!cause) throw error;
    if (
      !isOfflineAssessmentAccessDenied() &&
      previousOwner?.role === "patient" &&
      !previousOwner.locked &&
      (options.allowOfflineDiary ||
        (await store.getCachedDefinitions()).length > 0 ||
        (await hasCachedPatientPreferences(previousOwner.id)))
    ) {
      return { status: "offline", ownerId: previousOwner.id, cause };
    }
    return { status: "network-unavailable", cause };
  } finally {
    verificationQueryClient.removeQueries({ queryKey: verificationKey, exact: true });
  }

  if (!user) {
    if (previousOwner?.role === "patient" && !previousOwner.locked) {
      try {
        await store.lockOwnerPointer(previousOwner.id);
      } catch {
        denyOfflineAssessmentAccess();
      }
    }
    await lockMessagingAccess();
    await queryClient.cancelQueries();
    queryClient.clear();
    return { status: "unauthenticated" };
  }

  if (user.role !== "patient") {
    if (previousOwner?.role === "patient" && !previousOwner.locked) {
      try {
        await store.lockOwnerPointer(previousOwner.id);
      } catch {
        denyOfflineAssessmentAccess();
      }
    }
    await lockMessagingAccess();
    await queryClient.cancelQueries();
    queryClient.clear();
    return { status: "other-role", user };
  }

  const ownerUnchanged = await store.rememberOnlinePatientIfUnchanged(
    { id: user.id, role: user.role },
    previousOwner,
  );
  if (!ownerUnchanged) {
    await lockMessagingAccess();
    await queryClient.cancelQueries();
    queryClient.clear();
    return { status: "unauthenticated" };
  }
  try {
    await confirmMessagingOwner(user.id, "patient");
  } catch {
    await lockMessagingAccess();
    await queryClient.cancelQueries();
    queryClient.clear();
    return { status: "unauthenticated" };
  }
  clearOfflineAssessmentAccessDenial();
  if (previousOwner?.id !== user.id || previousOwner.locked) {
    await queryClient.cancelQueries();
    queryClient.clear();
  }
  queryClient.setQueryData(ME_KEY, user);
  return { status: "online", user };
}

/** Текущий пользователь из кэша или с сервера; null — сессии нет. */
export function currentUser(queryClient: QueryClient): Promise<User | null> {
  return queryClient.ensureQueryData({
    queryKey: ME_KEY,
    queryFn: () => fetchCurrentUser(fetchClient),
  });
}
