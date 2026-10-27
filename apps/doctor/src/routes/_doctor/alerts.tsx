import { createFileRoute, Outlet, useParams } from "@tanstack/react-router";
import { showsFailure } from "../../card/loadState";
import { usePatientNames } from "../../card/patientNames";
import { useAlerts } from "../../card/queries";
import { SignalList, type SignalsSearch } from "../../components/signals/SignalList";

/**
 * Экран «Сигналы» (спека #95, раздел 5; кадры «Врач v4 · 03» и «03б»): список слева,
 * подробности справа. `?patient=` — сигналы одного пациента, `?tab=resolved` — решённые.
 */
export const Route = createFileRoute("/_doctor/alerts")({
  validateSearch: (search: Record<string, unknown>): SignalsSearch => ({
    tab: search.tab === "resolved" ? "resolved" : undefined,
    patient: typeof search.patient === "string" ? search.patient : undefined,
  }),
  component: SignalsScreen,
});

function SignalsScreen() {
  const search = Route.useSearch();
  const { alertId } = useParams({ strict: false });
  const alerts = useAlerts(search.tab ?? "open", search.patient);
  const names = usePatientNames(alerts.data ?? []);

  return (
    <div className="flex min-h-0 min-w-0 flex-1">
      <SignalList
        alerts={alerts.data ?? []}
        names={names}
        search={search}
        selectedId={alertId}
        loading={alerts.isPending}
        failed={showsFailure(alerts)}
      />
      <div className="flex min-h-0 min-w-0 flex-1 overflow-y-auto">
        <Outlet />
      </div>
    </div>
  );
}
