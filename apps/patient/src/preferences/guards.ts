import type { QueryClient } from "@tanstack/react-query";
import { redirect } from "@tanstack/react-router";
import type { PatientSession } from "../session";
import { fetchPatientPreferences, patientPreferencesQueryKey } from "./api";

/** Confirm first-run completion before opening any protected patient destination. */
export async function guardPatientPreferences(
  queryClient: QueryClient,
  session: PatientSession,
  allowOnboarding = false,
): Promise<void> {
  if (session.status !== "online") return;
  const ownerId = session.user.id;
  const preferences = await queryClient.fetchQuery({
    queryKey: patientPreferencesQueryKey(ownerId),
    queryFn: () => fetchPatientPreferences(ownerId),
    staleTime: 30_000,
    retry: false,
  });
  if (!allowOnboarding && !preferences.onboarding_completed_at) {
    throw redirect({ to: "/onboarding", replace: true });
  }
}
