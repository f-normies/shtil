import { createFileRoute } from "@tanstack/react-router";
import { DiaryEntryScreen } from "../diary/DiaryEntryScreen";
import { requirePatientSession } from "../diary/route-guard";

export const Route = createFileRoute("/diary/$entryId")({
  beforeLoad: ({ context }) => requirePatientSession(context.queryClient),
  component: DiaryEntryPage,
});

function DiaryEntryPage() {
  const { session } = Route.useRouteContext();
  const { entryId } = Route.useParams();
  return <DiaryEntryScreen session={session} entryId={entryId} />;
}
