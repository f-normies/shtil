import type { Schemas } from "@compass/api-client";
import { StatusIcon } from "@compass/ui";
import { Link } from "@tanstack/react-router";
import { whenText } from "../../card/alerts";
import { isIngestion } from "../../card/facts";
import { caseHistoryDocument, isTherapy, proposalView } from "../../card/proposals";
import { Section } from "../signals/Section";
import { EventList, EventRow, ROW_LINK } from "./EventRow";

type Proposal = Schemas["ProposalOut"];

/**
 * «Предложения системы» в обзоре (спека #13, раздел 10.3): только ждущие решения, новые
 * выше, в пунктире. Строка ведёт туда, где предложение решается: факт истории болезни —
 * к документу, терапия — в «Терапию», остальное — в «Предложения». Пусто — группы нет.
 */
export function ProposalsSection({
  patientId,
  pending,
}: {
  patientId: string;
  pending: readonly Proposal[];
}) {
  if (pending.length === 0) {
    return null;
  }
  const now = new Date();
  return (
    <Section title="Предложения системы" aside={`ждут решения: ${pending.length}`} proposal flush>
      <EventList>
        {pending.map((p) => {
          const view = proposalView(p);
          const row = (
            <EventRow
              icon={<StatusIcon status="proposed" label="Предложение системы" />}
              title={view.title}
              detail={view.summary ?? view.recordLabel}
              time={whenText(p.created_at, now)}
            />
          );
          return (
            <li key={p.id}>
              {isIngestion(p) ? (
                <Link
                  className={ROW_LINK}
                  to="/patients/$patientId/documents"
                  params={{ patientId }}
                  search={{ document: caseHistoryDocument(p) ?? undefined }}
                >
                  {row}
                </Link>
              ) : (
                <Link
                  className={ROW_LINK}
                  to={
                    isTherapy(p) ? "/patients/$patientId/therapy" : "/patients/$patientId/proposals"
                  }
                  params={{ patientId }}
                >
                  {row}
                </Link>
              )}
            </li>
          );
        })}
      </EventList>
    </Section>
  );
}
