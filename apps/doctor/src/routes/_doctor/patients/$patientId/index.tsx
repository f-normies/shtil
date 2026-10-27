import { createFileRoute, Link } from "@tanstack/react-router";
import { isIngestion } from "../../../../card/facts";
import { buildFeed } from "../../../../card/feed";
import { pendingProposals } from "../../../../card/proposals";
import {
  useAssessmentHistory,
  usePatient,
  usePatientAlerts,
  usePatientProposals,
  useReactionReports,
} from "../../../../card/queries";
import { needsDecision } from "../../../../card/signals";
import { FeedSection } from "../../../../components/details/FeedSection";
import { ProposalsSection } from "../../../../components/details/ProposalsSection";
import { SignalGroup } from "../../../../components/details/SignalGroup";
import { StateSection } from "../../../../components/details/StateSection";
import { CaseHistoryPanel } from "../../../../components/documents/CaseHistoryPanel";
import { Section } from "../../../../components/signals/Section";
import { SummaryGroup } from "../../../../components/summary/SummaryGroup";
import { Medications } from "../../../../components/therapy/Medications";
import { Reactions } from "../../../../components/therapy/Reactions";

/**
 * «Обзор» карточки (спека #13, раздел 10.3; кадр «Врач v4 · 05»): что требует решения,
 * сводка с прошлого приёма (спека #38), состояние, предложения системы,
 * истории болезни (спека #30) и лента событий по дням.
 * Заместителю по сигналу (спека #56) — только сигнал, состояние и лента без предложений:
 * предложения и истории болезни ему закрыты.
 */
export const Route = createFileRoute("/_doctor/patients/$patientId/")({
  component: Overview,
});

function Overview() {
  const { patientId } = Route.useParams();
  const patient = usePatient(patientId);
  // Пока профиль не загружен, неизвестно, ведущий ли это врач: закрытые заместителю
  // разделы не запрашиваем, иначе в журнале появятся ложные отказы.
  if (!patient.data) {
    return null;
  }
  return patient.data.access === "deputy" ? <DeputyOverview /> : <LeadOverview />;
}

function DeputyOverview() {
  const { patientId } = Route.useParams();
  const { user } = Route.useRouteContext();
  const alerts = usePatientAlerts(patientId);
  const history = useAssessmentHistory(patientId);
  const decide = needsDecision(alerts.data ?? []);
  const feed = buildFeed({
    assessments: history.data ?? [],
    alerts: alerts.data ?? [],
    proposals: [],
    currentDoctorId: user.id,
  });
  return (
    <>
      {decide.map((a) => (
        <SignalGroup key={a.id} alert={a} />
      ))}
      <StateSection patientId={patientId} history={history} allowAdherence={false} />
      <DeputyMedicationDetails patientId={patientId} />
      <FeedSection events={feed} />
    </>
  );
}

/** Сведения минимального набора заместителя без инструментов изменения терапии. */
function DeputyMedicationDetails({ patientId }: { patientId: string }) {
  const reports = useReactionReports(patientId);
  const prescriptions = [...new Set((reports.data ?? []).map((r) => r.prescription_id))];
  return (
    <>
      <Section title="Препараты вне системы">
        <Medications patientId={patientId} readOnly />
      </Section>
      <Section title="Нежелательные реакции">
        {reports.isPending && <p className="text-sm text-ink-muted">Загрузка…</p>}
        {reports.isError && <p className="text-sm text-danger">Не удалось загрузить реакции.</p>}
        {reports.data?.length === 0 && (
          <p className="text-sm text-ink-muted">Отметок реакций нет.</p>
        )}
        {prescriptions.map((id) => (
          <Reactions
            key={id}
            prescriptionId={id}
            reports={(reports.data ?? []).filter((r) => r.prescription_id === id)}
            canDraft={false}
          />
        ))}
      </Section>
    </>
  );
}

function LeadOverview() {
  const { patientId } = Route.useParams();
  const { user } = Route.useRouteContext();
  const alerts = usePatientAlerts(patientId);
  const proposals = usePatientProposals(patientId);
  const history = useAssessmentHistory(patientId);
  const decide = needsDecision(alerts.data ?? []);
  const pending = pendingProposals(proposals.data ?? []);
  const notResolved = (alerts.data ?? []).filter((a) => a.state !== "resolved").length;
  const feed = buildFeed({
    assessments: history.data ?? [],
    alerts: alerts.data ?? [],
    proposals: proposals.data ?? [],
    currentDoctorId: user.id,
  });

  return (
    <>
      {alerts.isError && (
        <p className="flex items-center justify-between rounded-xl bg-surface px-4 py-3 text-sm text-danger">
          Не удалось загрузить сигналы.
          <button
            type="button"
            className="font-semibold text-primary-text"
            onClick={() => void alerts.refetch()}
          >
            Повторить
          </button>
        </p>
      )}
      {decide.length > 0 && (
        <section aria-labelledby="decide" className="flex flex-col gap-2">
          <h2
            id="decide"
            className="flex justify-between px-4 text-xs font-semibold tracking-wide text-ink-muted uppercase"
          >
            Требует решения
            <Link
              className="font-semibold tracking-normal text-primary-text normal-case hover:underline"
              to="/alerts"
              search={{ patient: patientId }}
            >
              Все сигналы ({notResolved})
            </Link>
          </h2>
          <div className="flex flex-col gap-3">
            {decide.map((a) => (
              <SignalGroup key={a.id} alert={a} />
            ))}
          </div>
        </section>
      )}

      {decide.length === 0 && (alerts.data ?? []).length > 0 && (
        // История сигналов пациента доступна из карточки и без открытых (спека #13, 10.12).
        <Link
          className="self-end text-sm font-semibold text-primary-text hover:underline"
          to="/alerts"
          search={{ patient: patientId, tab: notResolved === 0 ? "resolved" : undefined }}
        >
          {notResolved === 0 ? "Решённые сигналы" : `Все сигналы (${notResolved})`}
        </Link>
      )}

      <SummaryGroup patientId={patientId} currentDoctorId={user.id} />

      <StateSection patientId={patientId} history={history} />

      {proposals.isError && (
        <p className="flex items-center justify-between rounded-xl bg-surface px-4 py-3 text-sm text-danger">
          Не удалось загрузить предложения.
          <button
            type="button"
            className="font-semibold text-primary-text"
            onClick={() => void proposals.refetch()}
          >
            Повторить
          </button>
        </p>
      )}
      <ProposalsSection patientId={patientId} pending={pending} />

      <CaseHistoryPanel patientId={patientId} pending={pending.filter(isIngestion).length} />

      <FeedSection events={feed} />
    </>
  );
}
