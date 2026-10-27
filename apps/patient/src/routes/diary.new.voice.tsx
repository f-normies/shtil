import { createFileRoute } from "@tanstack/react-router";
import { QuickVoiceScreen } from "../diary/QuickVoiceScreen";
import { requirePatientSession } from "../diary/route-guard";

export const Route = createFileRoute("/diary/new/voice")({
  beforeLoad: ({ context }) => requirePatientSession(context.queryClient),
  component: QuickVoicePage,
});

function QuickVoicePage() {
  const { session } = Route.useRouteContext();
  return <QuickVoiceScreen session={session} />;
}
