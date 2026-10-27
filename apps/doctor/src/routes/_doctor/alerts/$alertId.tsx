import { createFileRoute } from "@tanstack/react-router";
import { useAlerts } from "../../../card/queries";
import { SignalDetails } from "../../../components/signals/SignalDetails";

/** Подробности сигнала. Сигнал ищется среди открытых и решённых того же отбора. */
export const Route = createFileRoute("/_doctor/alerts/$alertId")({
  component: SignalPage,
});

function SignalPage() {
  const { alertId } = Route.useParams();
  const { patient } = Route.useSearch();
  const { user } = Route.useRouteContext();
  const open = useAlerts("open", patient);
  const resolved = useAlerts("resolved", patient);
  const alert = [...(open.data ?? []), ...(resolved.data ?? [])].find((a) => a.id === alertId);

  if (alert) {
    return <SignalDetails key={alert.id} alert={alert} doctor={user} />;
  }
  const pending = open.isPending || resolved.isPending;
  return (
    <p className="flex-1 px-8 py-6 text-ink-muted">
      {pending ? "Загружаем сигнал…" : "Сигнал не найден."}
    </p>
  );
}
