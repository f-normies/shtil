import type { Schemas } from "@compass/api-client";
import { StatusIcon } from "@compass/ui";
import { Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";
import { adherenceFindings, patientGenitive, recordedAt, type Sex } from "../../card/adherence";
import { formatDate, formatDateTime } from "../../card/format";
import { proposalView } from "../../card/proposals";
import { Quote } from "../proposals/Quote";
import { PROPOSAL_OUTLINE, Section } from "../signals/Section";

type Proposal = Schemas["ProposalOut"];
type Period = NonNullable<Schemas["AdherenceOut"]["period"]>;

/**
 * «Из записей пациентки о приёме» (кадр 07): находки `freetext` категории `adherence`
 * за период. Ждущие решения — в пунктире, «Разобрать» открывает их в «Предложениях»;
 * подтверждённые — сплошные, «Подтверждено врачом». Нет записей — группы нет.
 */
export function FindingsSection({
  patientId,
  proposals,
  period,
  sex,
}: {
  patientId: string;
  proposals: readonly Proposal[];
  period: Period;
  sex: Sex;
}) {
  const items = adherenceFindings(proposals, period);
  if (items.length === 0) {
    return null;
  }
  return (
    <Section title={`Из записей ${patientGenitive(sex)} о приёме`} flush>
      <ul className="flex flex-col gap-2 p-1">
        {items.map((p) => (
          <li key={p.id}>
            <Finding patientId={patientId} proposal={p} />
          </li>
        ))}
      </ul>
    </Section>
  );
}

function Finding({ patientId, proposal }: { patientId: string; proposal: Proposal }) {
  const view = proposalView(proposal);
  const proposed = proposal.status === "proposed";
  const title = view.summary ?? (view.adherence ? `Приём: ${view.adherence}` : view.title);
  const origin = proposed ? "Предложение системы" : "Подтверждено врачом";
  return (
    <article
      className={`flex flex-col gap-2 rounded-lg px-3 py-3 ${proposed ? PROPOSAL_OUTLINE : ""}`}
    >
      <header className="flex items-start gap-3">
        <StatusIcon status={proposed ? "proposed" : "confirmed"} />
        <div className="min-w-0 flex-1">
          <h3 className="text-[15px] leading-5 text-ink">{title}</h3>
          <p className="text-[13px] leading-[18px] text-ink-muted">{origin}</p>
        </div>
        <time dateTime={recordedAt(proposal)} className="text-[13px] text-ink-muted">
          {formatDate(recordedAt(proposal))}
        </time>
      </header>
      {view.quote && (
        <div className="pl-8">
          <Quote
            text={view.quote}
            caption={[view.recordLabel, formatDateTime(recordedAt(proposal))]
              .filter(Boolean)
              .join(", ")}
          />
        </div>
      )}
      {proposed && (
        <Link
          to="/patients/$patientId/proposals"
          params={{ patientId }}
          search={{ proposal: proposal.id }}
          className="ml-8 flex w-fit items-center gap-1 text-[15px] font-semibold text-primary-text"
        >
          Разобрать
          <ChevronRight aria-hidden className="size-4" />
        </Link>
      )}
    </article>
  );
}
