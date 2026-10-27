import { createFileRoute } from "@tanstack/react-router";
import { ProfilePage } from "../preferences/ProfilePage";

export const Route = createFileRoute("/profile/")({
  component: () => <ProfilePage session={Route.useRouteContext().session} />,
});
