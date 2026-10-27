import { homePathFor } from "@compass/api-client";
import { createFileRoute, redirect } from "@tanstack/react-router";
import { guardPatientPreferences } from "../preferences/guards";
import { OnboardingPage } from "../preferences/OnboardingPage";
import { parseOnboardingSearch } from "../preferences/onboarding-flow";
import { resolvePatientSession } from "../session";

export const Route = createFileRoute("/onboarding")({
  // Шаг в адресе: `?step=mood`. «Назад» браузера и обновление страницы сохраняют шаг (P1-13).
  validateSearch: parseOnboardingSearch,
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
    if (session.status === "offline") throw redirect({ to: "/profile", replace: true });
    await guardPatientPreferences(context.queryClient, session, true);
    return { session };
  },
  component: OnboardingRoute,
});

function OnboardingRoute() {
  const { session } = Route.useRouteContext();
  const { step } = Route.useSearch();
  return <OnboardingPage session={session} requestedStep={step} />;
}
