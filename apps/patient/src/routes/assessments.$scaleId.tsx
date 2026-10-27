import { homePathFor } from "@compass/api-client";
import { createFileRoute, redirect } from "@tanstack/react-router";
import { AssessmentForm } from "../assessments/AssessmentForm";
import { guardPatientPreferences } from "../preferences/guards";
import { useApplyPatientPreferences, usePatientPreferences } from "../preferences/hooks";
import { resolvePatientSession } from "../session";

export const Route = createFileRoute("/assessments/$scaleId")({
  beforeLoad: async ({ context }) => {
    const session = await resolvePatientSession(context.queryClient);
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
    await guardPatientPreferences(context.queryClient, session);
    return { session };
  },
  component: AssessmentPage,
});

function AssessmentPage() {
  const { session } = Route.useRouteContext();
  const preferences = usePatientPreferences(session);
  useApplyPatientPreferences(preferences.data);
  const { scaleId } = Route.useParams();
  const ownerId = session.status === "online" ? session.user.id : session.ownerId;
  return (
    <AssessmentForm
      key={`${ownerId}:${scaleId}`}
      ownerId={ownerId}
      scaleId={scaleId}
      session={session}
    />
  );
}
