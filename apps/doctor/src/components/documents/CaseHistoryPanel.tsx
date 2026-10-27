import { Link } from "@tanstack/react-router";
import { Plus } from "lucide-react";
import { statusView } from "../../card/documents";
import { useDocuments } from "../../card/queries";
import { EventList, EventRow, ROW_LINK } from "../details/EventRow";
import { Section } from "../signals/Section";
import { StatusIcon } from "./DocumentList";

const SHOWN = 3;

/**
 * Истории болезни в обзоре карточки: последние документы со статусом и вход на экран
 * загрузки и проверки фактов (кадр 07в, «Вход и выход»). Своего сегмента у экрана нет:
 * переключатель держит шесть разделов.
 */
export function CaseHistoryPanel({ patientId, pending }: { patientId: string; pending: number }) {
  const documents = useDocuments(patientId);
  const list = documents.data ?? [];
  const now = new Date();

  return (
    <Section
      title="История болезни"
      aside={pending > 0 ? `ждут решения: ${pending}` : undefined}
      flush
    >
      <EventList>
        {documents.isError && (
          <li className="px-4 py-3 text-sm text-danger">Не удалось загрузить документы.</li>
        )}
        {list.slice(0, SHOWN).map((d) => {
          const status = statusView(d, now);
          return (
            <li key={d.id}>
              <Link
                className={ROW_LINK}
                to="/patients/$patientId/documents"
                params={{ patientId }}
                search={{ document: d.id }}
              >
                <EventRow
                  icon={<StatusIcon tone={status.tone} selected={false} />}
                  title={<span className="break-words">{d.filename}</span>}
                  time={
                    <span className={status.tone === "failed" ? "text-danger" : undefined}>
                      {status.text}
                    </span>
                  }
                />
              </Link>
            </li>
          );
        })}
        <li>
          <Link className={ROW_LINK} to="/patients/$patientId/documents" params={{ patientId }}>
            <EventRow
              icon={<Plus aria-hidden className="size-5 text-primary-text" />}
              title={
                <span className="font-semibold text-primary-text">
                  {list.length > 0
                    ? `Загрузить и проверить истории болезни (${list.length})`
                    : "Загрузить историю болезни"}
                </span>
              }
              detail={
                documents.isSuccess && list.length === 0
                  ? "Система найдёт в выписке диагнозы, препараты, анамнез и факторы риска. Каждый факт вы подтвердите сами."
                  : undefined
              }
            />
          </Link>
        </li>
      </EventList>
    </Section>
  );
}
