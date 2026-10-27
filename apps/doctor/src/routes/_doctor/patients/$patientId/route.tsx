import { Alert, SegmentedControl } from "@compass/ui";
import { createFileRoute, Link, Outlet, useMatches, useNavigate } from "@tanstack/react-router";
import { ChevronLeft } from "lucide-react";
import { deputyAccessUntil } from "../../../../card/escalation";
import { patientLoadMessage } from "../../../../card/patientError";
import { usePatient, usePatientAlerts } from "../../../../card/queries";
import { type SectionRoute, sectionOf, sectionsFor } from "../../../../card/sections";
import { ConsentBanner } from "../../../../components/details/ConsentBanner";
import { PatientTitle } from "../../../../components/details/PatientTitle";

/**
 * Карточка пациента в окне v4 (спека #13, разделы 10.1 и 10.3): список свёрнут, колонка
 * 640 px по центру, тулбар закреплён. Истории болезни открываются из обзора, выбран «Обзор».
 * Заместитель по сигналу (спека #56) видит «Обзор» и «Шкалы» и срок своего доступа.
 */
export const Route = createFileRoute("/_doctor/patients/$patientId")({
  component: PatientCard,
});

function PatientCard() {
  const { patientId } = Route.useParams();
  const { user } = Route.useRouteContext();
  const patient = usePatient(patientId);
  const alerts = usePatientAlerts(patientId);
  const navigate = useNavigate();
  const deputy = patient.data?.access === "deputy";
  const until = deputy ? deputyAccessUntil(alerts.data ?? [], new Date()) : null;
  const section = useMatches({ select: (matches) => sectionOf(matches.at(-1)?.routeId) });

  return (
    <>
      <ConsentBanner revokedAt={patient.data?.consent_revoked_at} />
      <main className="min-h-0 flex-1 overflow-y-auto px-6">
        <div className="mx-auto flex w-full max-w-[640px] flex-col gap-6 pb-10">
          <header className="sticky top-0 z-10 flex flex-col gap-3 bg-canvas pt-6 pb-3">
            {deputy ? (
              // Пациента заместителя нет в его списке: назад — к сигналам.
              <Link
                to="/alerts"
                search={{}}
                className="flex h-9 w-fit items-center gap-1 text-sm font-semibold text-primary-text"
              >
                <ChevronLeft aria-hidden className="size-5" />
                Сигналы
              </Link>
            ) : (
              <Link
                to="/"
                search={{ patient: patientId }}
                className="flex h-9 w-fit items-center gap-1 text-sm font-semibold text-primary-text"
              >
                <ChevronLeft aria-hidden className="size-5" />
                Пациенты
              </Link>
            )}
            {patient.data && (
              <PatientTitle patient={patient.data} large doctorId={deputy ? undefined : user.id} />
            )}
            {until && <p className="text-sm font-semibold text-ink">{until}</p>}
            {patient.data && (
              <SegmentedControl
                ariaLabel="Разделы карточки"
                className="w-full"
                options={sectionsFor(patient.data.access)}
                value={section}
                onValueChange={(to) => navigate({ to: to as SectionRoute, params: { patientId } })}
              />
            )}
          </header>
          {patient.isPending && <p className="text-sm text-ink-muted">Загрузка…</p>}
          {patient.isError && (
            <div className="flex flex-col gap-3">
              <Alert tone="error">{patientLoadMessage(patient.error)}</Alert>
            </div>
          )}
          {patient.data && <Outlet />}
        </div>
      </main>
    </>
  );
}
