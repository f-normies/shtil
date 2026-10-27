import type { MessagingRole } from "@compass/api-client";
import type { QueryClient } from "@tanstack/react-query";
import { getConfirmedMessagingOwner } from "./access";

type TypingStatus = { patient_id: string; doctor_id: string; typing_until: string | null };

/**
 * Write a `message.typing` lease into the cached chat status of the peer's conversation.
 * The event reaches both sides, so the owner's own typing is ignored. Returns whether a
 * status query was updated; false also when the event is malformed.
 */
export function applyTypingEvent(
  queryClient: QueryClient,
  owner: { ownerId: string; role: MessagingRole },
  data?: Record<string, unknown>,
): boolean {
  const patientId = typeof data?.patient_id === "string" ? data.patient_id : undefined;
  const doctorId = typeof data?.doctor_id === "string" ? data.doctor_id : undefined;
  const userId = typeof data?.user_id === "string" ? data.user_id : undefined;
  const typingUntil = typeof data?.typing_until === "string" ? data.typing_until : null;
  if (!patientId || !doctorId || !userId || userId === owner.ownerId) return false;
  const updated = queryClient.setQueriesData<TypingStatus>(
    {
      predicate: ({ queryKey }) =>
        queryKey[0] === "messaging" &&
        queryKey[1] === owner.ownerId &&
        queryKey[2] === owner.role &&
        (queryKey[3] === "status" || queryKey[3] === "bootstrap-status") &&
        queryKey[4] === patientId &&
        (queryKey[3] === "bootstrap-status" || queryKey[5] === doctorId),
    },
    (current) =>
      current && current.patient_id === patientId && current.doctor_id === doctorId
        ? { ...current, typing_until: typingUntil }
        : current,
  );
  return updated.length > 0;
}

/** Refresh chat queries for the active owner after an existing socket event. */
export async function refreshMessaging(
  queryClient: QueryClient,
  eventType: string,
  data?: Record<string, unknown>,
): Promise<void> {
  if (eventType !== "ready" && !eventType.startsWith("message.")) return;
  const owner = getConfirmedMessagingOwner();
  if (!owner) return;
  if (eventType === "message.typing") {
    // A typing lease is a few seconds long: the status refetch interval would miss it.
    applyTypingEvent(queryClient, owner, data);
    return;
  }

  const patientId = typeof data?.patient_id === "string" ? data.patient_id : undefined;
  const doctorId = typeof data?.doctor_id === "string" ? data.doctor_id : undefined;
  await queryClient.invalidateQueries({
    predicate: ({ queryKey }) => {
      if (
        queryKey[0] !== "messaging" ||
        queryKey[1] !== owner.ownerId ||
        queryKey[2] !== owner.role
      ) {
        return false;
      }
      if (!patientId || eventType === "ready") return true;
      const kind = queryKey[3];
      if (kind === "unread") return true;
      const queryPatientId =
        kind === "history" || kind === "status"
          ? queryKey[4]
          : kind === "bootstrap-status"
            ? queryKey[4]
            : undefined;
      if (queryPatientId !== patientId) return false;
      const queryDoctorId = kind === "history" || kind === "status" ? queryKey[5] : undefined;
      return !doctorId || !queryDoctorId || queryDoctorId === doctorId;
    },
  });
}
