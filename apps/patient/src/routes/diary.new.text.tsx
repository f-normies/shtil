import { createFileRoute } from "@tanstack/react-router";
import { QuickTextScreen } from "../diary/QuickTextScreen";
import { requirePatientSession } from "../diary/route-guard";

export const Route = createFileRoute("/diary/new/text")({
  beforeLoad: ({ context }) => requirePatientSession(context.queryClient),
  component: QuickTextPage,
});

function QuickTextPage() {
  const { session } = Route.useRouteContext();
  return <QuickTextScreen session={session} />;
}
