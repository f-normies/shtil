import { homePathFor } from "@compass/api-client";
import { createFileRoute, Outlet, redirect } from "@tanstack/react-router";
import { guardPatientPreferences } from "../preferences/guards";
import { resolvePatientSession } from "../session";

export const Route = createFileRoute("/profile")({
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
  component: () => <Outlet />,
});
