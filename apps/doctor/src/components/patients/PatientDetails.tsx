import type { Schemas } from "@compass/api-client";
import { Link } from "@tanstack/react-router";
import { whenText } from "../../card/alerts";
import { buildFeed } from "../../card/feed";
import { useAssessmentHistory, usePatientAlerts, usePatientProposals } from "../../card/queries";
import { needsDecision } from "../../card/signals";
import { EventList, EventRow } from "../details/EventRow";
import { FEED_ICON } from "../details/FeedSection";
import { PatientTitle } from "../details/PatientTitle";
import { SignalGroup } from "../details/SignalGroup";
import { StateSection } from "../details/StateSection";
import { Section } from "../signals/Section";

/** «Последнее» — 2–3 события ленты (спека #13, раздел 10.2). */
const LAST_EVENTS = 3;

/**
 * Подробности выбранного пациента справа от списка (спека #13, раздел 10.2): самый срочный
 * сигнал с действием, состояние, последние события и «Открыть карточку».
 */
export function PatientDetails({
  patient,
  doctorId,
}: {
  patient: Schemas["PatientSummary"];
  doctorId: string;
}) {
  const alerts = usePatientAlerts(patient.id);
  const proposals = usePatientProposals(patient.id);
  const history = useAssessmentHistory(patient.id);
  const top = needsDecision(alerts.data ?? [])[0];
  const now = new Date();
  const last = buildFeed({
    assessments: history.data ?? [],
    alerts: alerts.data ?? [],
    proposals: proposals.data ?? [],
    currentDoctorId: doctorId,
  }).slice(0, LAST_EVENTS);

  return (
    <div className="flex max-w-[688px] animate-fade-in flex-col gap-6 px-8 pt-6 pb-10 motion-reduce:animate-none">
      <PatientTitle patient={patient} />
      {top && (
        <section aria-labelledby="top-signal" className="flex flex-col gap-2">
          <h2
            id="top-signal"
            className="px-4 text-xs font-semibold tracking-wide text-ink-muted uppercase"
          >
            Требует решения
          </h2>
          <SignalGroup alert={top} />
        </section>
      )}
      <StateSection patientId={patient.id} history={history} />
      {last.length > 0 && (
        <Section title="Последнее" flush>
          <EventList>
            {last.map((e) => (
              <li key={e.key}>
                <EventRow icon={FEED_ICON[e.kind]} title={e.text} time={whenText(e.at, now)} />
              </li>
            ))}
          </EventList>
        </Section>
      )}
      <Link
        to="/patients/$patientId"
        params={{ patientId: patient.id }}
        className="ui-button self-start"
        data-variant="primary"
      >
        Открыть карточку
      </Link>
    </div>
  );
}
