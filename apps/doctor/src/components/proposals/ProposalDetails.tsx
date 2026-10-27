import { StatusBadge } from "@compass/ui";
import { Link } from "@tanstack/react-router";
import { CircleDotDashed, X } from "lucide-react";
import { formatDateTime } from "../../card/format";
import {
  decidedElsewhere,
  type Outcome,
  outcomeText,
  type QueueItem,
  quoteCaption,
  therapyBasis,
  upperFirst,
} from "../../card/proposalQueue";
import { proposalView } from "../../card/proposals";
import { usePatient, usePatientAlerts } from "../../card/queries";
import { REFERRAL } from "../../card/referral";
import { alertBasis } from "../../card/sources";
import { PatientHeader } from "../details/PatientHeader";
import { PatientTitle } from "../details/PatientTitle";
import { SourceLine } from "../source/SourceLine";
import { Decision } from "./Decision";
import { ApplicabilitySection, RelatedSection, SourceSection } from "./Facts";
import { Quote } from "./Quote";

const CARD = {
  // Ждущее решения — пунктир primary-text и значок «Предложение системы» (кадр 04).
  proposed: "border border-dashed border-primary-text",
  confirmed: "border border-line",
  rejected: "border border-line",
} as const;

const ELSEWHERE = {
  therapy: {
    label: "Рассмотреть в разделе «Терапия»",
    note: "Назначение подтверждается в «Терапии», где видны все проверки по инструкции.",
  },
  signal: {
    label: "Открыть сигнал",
    note: "Направление решается на экране сигнала вместе с другими сигналами пациента.",
  },
  documents: {
    label: "Рассмотреть в истории болезни",
    note: "Факт подтверждается рядом с документом, цитатой и сверкой.",
  },
} as const;

const LINK =
  "inline-flex min-h-9 items-center rounded-md border border-line bg-surface px-4 text-[15px] font-semibold text-ink hover:bg-canvas focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus";

/**
 * Подробности предложения (кадр 04, справа; кадр 07б): что предлагается и основание, цитата
 * пациента, источник, применимость и связанные предложения. Ждущее решения — с кнопками,
 * решённое — с тем, кто и когда решил.
 */
export function ProposalDetails({
  item,
  position,
  related,
  showPatient,
  doctorId,
  guard,
  onDecided,
  onOpen,
  now,
}: {
  item: QueueItem;
  position: { index: number; total: number } | null;
  related: QueueItem[];
  showPatient: boolean;
  doctorId: string;
  /** Пункт выбран автоматически после решения: кнопки решения включаются не сразу. */
  guard: boolean;
  onDecided: (id: string, outcome: Outcome, title: string) => void;
  onOpen: (id: string) => void;
  now: Date;
}) {
  const view = proposalView(item);
  const basis = therapyBasis(item);
  const place = item.status === "proposed" ? decidedElsewhere(item) : null;

  return (
    <article
      aria-label={`Предложение: ${view.title}`}
      className="flex min-w-0 flex-col gap-6 motion-safe:animate-fade-in motion-reduce:animate-fade-in"
    >
      {showPatient && (
        <PatientBar patientId={item.patient_id} name={item.patient_name} doctorId={doctorId} />
      )}

      <section className="flex flex-col gap-2">
        <h2 className="px-4 text-xs font-semibold tracking-[0.4px] text-ink-muted uppercase">
          {position ? `Предложение ${position.index} из ${position.total}` : "Предложение"}
        </h2>
        <div className={`flex flex-col gap-3 rounded-xl bg-surface p-4 ${CARD[item.status]}`}>
          <p className="flex items-center justify-between gap-3 text-sm">
            <StatusLabel status={item.status} />
            <time dateTime={item.created_at} className="text-[13px] text-ink-muted">
              {formatDateTime(item.created_at, now)}
            </time>
          </p>
          <h3 className="text-xl leading-[26px] font-semibold text-ink">{view.title}</h3>
          {view.summary && (
            <div className="flex flex-col gap-0.5">
              <p className="text-[13px] leading-[18px] text-ink-muted">Основание</p>
              <p className="text-[15px] leading-[22px] text-ink">{upperFirst(view.summary)}</p>
            </div>
          )}
          {view.fields.length > 0 && (
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 text-sm">
              {view.fields.map((f) => (
                <div key={f.name} className="contents">
                  <dt className="text-ink-muted">{f.name}</dt>
                  <dd className="break-words">{f.value}</dd>
                </div>
              ))}
            </dl>
          )}
          {view.quote && <Quote text={view.quote} caption={quoteCaption(item)} />}
          {basis && <SourceLine basis={basis} />}
          {item.kind === REFERRAL && <ReferralSource item={item} />}
          {item.status !== "proposed" ? (
            <DecisionResult item={item} doctorId={doctorId} />
          ) : place ? (
            <div className="flex flex-col gap-2">
              <p className="text-[13px] leading-[18px] text-ink-muted">{ELSEWHERE[place].note}</p>
              <div>
                <ElsewhereLink item={item} place={place} />
              </div>
            </div>
          ) : (
            <Decision
              item={item}
              guard={guard}
              onDecided={(outcome) => onDecided(item.id, outcome, view.title)}
            />
          )}
        </div>
      </section>

      <SourceSection item={item} />
      <ApplicabilitySection item={item} />
      <RelatedSection related={related} onOpen={onOpen} />
    </article>
  );
}

function StatusLabel({ status }: { status: QueueItem["status"] }) {
  if (status === "confirmed") {
    return <StatusBadge status="confirmed" />;
  }
  if (status === "rejected") {
    return (
      <span className="flex items-center gap-2 font-semibold text-ink-muted">
        <X aria-hidden className="size-5" />
        Отклонено
      </span>
    );
  }
  return (
    <span className="flex items-center gap-2 font-semibold text-primary-text">
      <CircleDotDashed aria-hidden className="size-5" />
      Предложение системы
    </span>
  );
}

function DecisionResult({ item, doctorId }: { item: QueueItem; doctorId: string }) {
  const { text, reason } = outcomeText(item, doctorId, formatDateTime);
  return (
    <div className="flex flex-col gap-0.5 border-t border-separator pt-3 text-[15px] leading-[22px]">
      <p className="text-ink">{text}</p>
      {reason && <p className="text-ink-muted">{reason}</p>}
    </div>
  );
}

function ElsewhereLink({ item, place }: { item: QueueItem; place: keyof typeof ELSEWHERE }) {
  const label = ELSEWHERE[place].label;
  const patientId = item.patient_id;
  if (place === "therapy") {
    return (
      <Link
        data-primary-action
        className={LINK}
        to="/patients/$patientId/therapy"
        params={{ patientId }}
      >
        {label}
      </Link>
    );
  }
  if (place === "signal") {
    return (
      <Link
        data-primary-action
        className={LINK}
        to="/alerts/$alertId"
        params={{ alertId: String(item.payload.alert_id ?? "") }}
        search={{ patient: patientId }}
      >
        {label}
      </Link>
    );
  }
  return (
    <Link
      data-primary-action
      className={LINK}
      to="/patients/$patientId/documents"
      params={{ patientId }}
      search={{ document: String(item.payload.document_id ?? "") }}
    >
      {label}
    </Link>
  );
}

/** Источник направления — правило сигнала, по которому оно создано (инспектор #106). */
function ReferralSource({ item }: { item: QueueItem }) {
  const alerts = usePatientAlerts(item.patient_id);
  const alert = alerts.data?.find((a) => a.id === item.payload.alert_id);
  const basis = alert ? alertBasis(alert) : null;
  return basis ? <SourceLine basis={basis} /> : null;
}

/** Пациент предложения на экране «Предложения»: кто это и как связаться. */
function PatientBar({
  patientId,
  name,
  doctorId,
}: {
  patientId: string;
  name: string | undefined;
  doctorId: string;
}) {
  const patient = usePatient(patientId);
  const card = (
    <Link className={LINK} to="/patients/$patientId/proposals" params={{ patientId }} search={{}}>
      Открыть карточку
    </Link>
  );
  return patient.data ? (
    <PatientTitle patient={patient.data} large doctorId={doctorId} actions={card} />
  ) : (
    <PatientHeader name={name ?? "Пациент"} about="" large actions={card} />
  );
}
