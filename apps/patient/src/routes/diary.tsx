import { createFileRoute, Outlet } from "@tanstack/react-router";
import { requirePatientSession } from "../diary/route-guard";

export const Route = createFileRoute("/diary")({
  beforeLoad: ({ context }) => requirePatientSession(context.queryClient),
  component: DiaryPage,
});

function DiaryPage() {
  return <Outlet />;
}
