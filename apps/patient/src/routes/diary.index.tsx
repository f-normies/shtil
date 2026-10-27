import { createFileRoute } from "@tanstack/react-router";
import { DiaryScreen } from "../diary/DiaryScreen";
import { requirePatientSession } from "../diary/route-guard";

export const Route = createFileRoute("/diary/")({
  beforeLoad: ({ context }) => requirePatientSession(context.queryClient),
  component: DiaryIndexPage,
});

function DiaryIndexPage() {
  const { session } = Route.useRouteContext();
  return <DiaryScreen session={session} />;
}
