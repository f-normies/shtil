import { createFileRoute } from "@tanstack/react-router";
import { requirePatientSession } from "../diary/route-guard";
import { TodayScreen } from "../diary/TodayScreen";
import { usePatientHelp } from "../help/api";
import { guardPatientPreferences } from "../preferences/guards";
import { useApplyPatientPreferences, usePatientPreferences } from "../preferences/hooks";

export const Route = createFileRoute("/")({
  beforeLoad: async ({ context }) => {
    const result = await requirePatientSession(context.queryClient);
    await guardPatientPreferences(context.queryClient, result.session);
    return result;
  },
  component: PatientHome,
});

function PatientHome() {
  usePatientHelp();
  const { session } = Route.useRouteContext();
  const preferences = usePatientPreferences(session);
  useApplyPatientPreferences(preferences.data);
  return <TodayScreen session={session} displayName={preferences.data?.display_name} />;
}
