import { Button } from "@compass/ui";
import { createFileRoute, Link } from "@tanstack/react-router";
import { usePatient, usePatientAdherence, usePatientProposals } from "../../../../card/queries";
import { DoseCalendar } from "../../../../components/adherence/DoseCalendar";
import { FindingsSection } from "../../../../components/adherence/FindingsSection";
import { ShareSection } from "../../../../components/adherence/ShareSection";
import { Section } from "../../../../components/signals/Section";

/**
 * «Приём» по кадру «Врач v4 · 07» (спека #34, раздел 6): доля доз за 14 дней по отметкам
 * пациента, дозы по дням и записи пациента о приёме. Период 14 дней фиксирован.
 */
export const Route = createFileRoute("/_doctor/patients/$patientId/adherence")({
  component: Adherence,
});

function Adherence() {
  const { patientId } = Route.useParams();
  const patient = usePatient(patientId);
  const adherence = usePatientAdherence(patientId);
  const proposals = usePatientProposals(patientId);
  const sex = patient.data?.sex;

  if (adherence.isPending) {
    return (
      <div
        aria-busy
        className="flex animate-pulse flex-col gap-3 rounded-xl bg-surface p-4 motion-reduce:animate-none"
      >
        <span className="h-4 w-48 rounded bg-surface-sunken" />
        <span className="h-10 w-40 rounded bg-surface-sunken" />
        <span className="h-2 rounded bg-surface-sunken" />
        <span className="h-24 rounded bg-surface-sunken" />
      </div>
    );
  }
  if (adherence.isError) {
    return (
      <p className="flex items-center justify-between rounded-xl bg-surface px-4 py-3 text-sm text-ink">
        Не удалось загрузить приём
        <Button variant="link" onClick={() => void adherence.refetch()}>
          Повторить
        </Button>
      </p>
    );
  }
  const data = adherence.data;
  if (data.course === "none" || !data.period) {
    return (
      <Section title="Приём">
        <p className="flex items-center justify-between text-[15px] text-ink">
          Нет действующих назначений
          <Link
            to="/patients/$patientId/therapy"
            params={{ patientId }}
            className="font-semibold text-primary-text"
          >
            Открыть «Терапию»
          </Link>
        </p>
      </Section>
    );
  }
  return (
    <>
      <ShareSection adherence={data} sex={sex} />
      <DoseCalendar adherence={data} sex={sex} />
      {proposals.isError ? (
        <p className="flex items-center justify-between rounded-xl bg-surface px-4 py-3 text-sm text-ink">
          Не удалось загрузить записи о приёме
          <Button variant="link" onClick={() => void proposals.refetch()}>
            Повторить
          </Button>
        </p>
      ) : (
        <FindingsSection
          patientId={patientId}
          proposals={proposals.data ?? []}
          period={data.period}
          sex={sex}
        />
      )}
    </>
  );
}
