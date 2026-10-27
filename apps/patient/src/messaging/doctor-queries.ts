import {
  createMessagingApi,
  fetchClient,
  MessagingApiError,
  type MessagingStatus,
  messagingKeys,
} from "@compass/api-client";
import { useQuery } from "@tanstack/react-query";
import type { PatientSession } from "../session";

function onlinePatientId(session: PatientSession): string | null {
  return session.status === "online" ? session.user.id : null;
}

async function readDoctorStatus(patientId: string, signal?: AbortSignal): Promise<MessagingStatus> {
  const result = await fetchClient.GET("/api/v1/patients/{patient_id}/messaging-status", {
    params: { path: { patient_id: patientId } },
    signal,
  });
  if (!result.response.ok || result.error !== undefined || !result.data) {
    throw new MessagingApiError(result.response.status, undefined, "Статус переписки недоступен");
  }
  return result.data;
}

/** The doctor's name and presence; shares its cache key with the chat screen. */
export function useDoctorStatus(session: PatientSession) {
  const patientId = onlinePatientId(session);
  const owner = { ownerId: patientId ?? "unowned", role: "patient" as const };
  return useQuery({
    queryKey: [...messagingKeys.owner(owner), "bootstrap-status", patientId ?? "unowned"] as const,
    queryFn: ({ signal }) => readDoctorStatus(patientId ?? "", signal),
    enabled: patientId !== null,
    retry: false,
    staleTime: 20_000,
  });
}

/** Unread doctor messages for the bottom navigation badge and the home card. */
export function useUnreadMessageCount(session: PatientSession): number {
  const patientId = onlinePatientId(session);
  const api = createMessagingApi();
  const query = useQuery({
    ...api.unreadOptions({ ownerId: patientId ?? "unowned", role: "patient" }),
    enabled: patientId !== null,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: "always",
    select: (items) => items.reduce((sum, item) => sum + item.unread_count, 0),
  });
  return query.data ?? 0;
}
