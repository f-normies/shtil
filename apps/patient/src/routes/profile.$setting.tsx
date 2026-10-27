import { homePathFor } from "@compass/api-client";
import { createFileRoute, redirect } from "@tanstack/react-router";
import { guardPatientPreferences } from "../preferences/guards";
import { type ProfileSetting, ProfileSettingPage } from "../preferences/ProfileSettingPage";
import { resolvePatientSession } from "../session";

const settings: ProfileSetting[] = [
  "name",
  "reminder",
  "mood",
  "story",
  "pills",
  "text",
  "consent",
];

export const Route = createFileRoute("/profile/$setting")({
  beforeLoad: async ({ context, params }) => {
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
    if (!settings.includes(params.setting as ProfileSetting)) {
      throw redirect({ to: "/profile", replace: true });
    }
    return { session };
  },
  component: () => {
    const { session } = Route.useRouteContext();
    const { setting } = Route.useParams();
    return <ProfileSettingPage session={session} setting={setting as ProfileSetting} />;
  },
});
