import { homePathFor } from "@compass/api-client";
import type { QueryClient } from "@tanstack/react-query";
import { redirect } from "@tanstack/react-router";
import { type PatientSession, resolvePatientSession } from "../session";

export type SignedInPatientSession = Extract<PatientSession, { status: "online" | "offline" }>;

/** Share the patient ownership gate across diary routes. */
export async function requirePatientSession(
  queryClient: QueryClient,
): Promise<{ session: SignedInPatientSession }> {
  const session = await resolvePatientSession(queryClient, { allowOfflineDiary: true });
  if (session.status === "unauthenticated" || session.status === "network-unavailable") {
    throw redirect({ to: "/login", replace: true });
  }
  if (session.status === "other-role") {
    throw redirect({
      href: homePathFor(session.user.role),
      reloadDocument: true,
      replace: true,
    });
  }
  return { session };
}
