import { createFileRoute } from "@tanstack/react-router";
import { parseMoodSearch } from "../diary/checkin-access";
import { DailyCheckInScreen } from "../diary/DailyCheckInScreen";
import { requirePatientSession } from "../diary/route-guard";

export const Route = createFileRoute("/check-in")({
  beforeLoad: ({ context }) => requirePatientSession(context.queryClient),
  // Число, не строка: строку «3» маршрутизатор записал бы в адрес как JSON «"3"» (P2-21).
  validateSearch: (search: Record<string, unknown>) => ({
    mood: parseMoodSearch(search.mood),
  }),
  component: CheckInPage,
});

function CheckInPage() {
  const { session } = Route.useRouteContext();
  const { mood } = Route.useSearch();
  return <DailyCheckInScreen session={session} initialMood={mood} />;
}
