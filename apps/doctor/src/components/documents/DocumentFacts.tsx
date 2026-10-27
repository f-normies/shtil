import type { Schemas } from "@compass/api-client";
import { Button } from "@compass/ui";
import { ArrowUpRight, Check, CircleCheck, X } from "lucide-react";
import { useState } from "react";
import { statusView } from "../../card/documents";
import {
  decidedView,
  documentFacts,
  factsSummary,
  knownView,
  nextPending,
  waitingLabel,
} from "../../card/facts";
import { FactRow, fileUrl } from "./FactRow";
import { Group, Row } from "./Group";

type DocumentOut = Schemas["DocumentOut"];
type Proposal = Schemas["ProposalOut"];

/**
 * Данные из выбранного документа (кадр 07в): ожидающие решения факты по группам,
 * «Уже в карточке» без кнопок и «Решённые» с автором и временем. Раскрыт один факт.
 */
export function DocumentFacts({
  document,
  proposals,
  me,
  now,
}: {
  document: DocumentOut;
  proposals: { data?: Proposal[]; isError: boolean; refetch: () => unknown };
  me: { id: string; full_name: string };
  now: Date;
}) {
  const facts = documentFacts(proposals.data ?? [], document);
  // undefined — врач ещё не раскрывал и не сворачивал: раскрыт первый факт, как в кадре.
  const [chosen, setChosen] = useState<string | null | undefined>(undefined);
  const open = chosen === undefined ? (facts.groups[0]?.pending[0]?.id ?? null) : chosen;
  const done = document.status === "done";
  const summary = done
    ? factsSummary(
        document,
        { pending: facts.pending, decided: facts.decided.length, known: facts.known.length },
        now,
      )
    : statusView(document, now).text;

  return (
    <>
      <div className="flex items-center gap-3 px-4">
        <div className="min-w-0 flex-1">
          <h3 className="text-[15px] leading-5 font-semibold break-words text-ink">
            Данные из документа «{document.filename}»
          </h3>
          <p className="text-[13px] leading-[18px] text-ink-muted">{summary}</p>
        </div>
        <a
          className="inline-flex shrink-0 items-center gap-1 text-sm font-semibold text-primary underline-offset-2 hover:underline"
          href={fileUrl(document)}
          target="_blank"
          rel="noopener"
        >
          <ArrowUpRight aria-hidden className="size-5" />
          Открыть документ
        </a>
      </div>

      {!done && <Pending document={document} />}
      {done && proposals.isError && (
        <div className="flex flex-wrap items-center gap-3 px-4">
          <p role="alert" className="text-sm text-danger">
            Ошибка: не удалось загрузить факты документа.
          </p>
          <Button variant="secondary" onClick={() => proposals.refetch()}>
            Повторить
          </Button>
        </div>
      )}

      {done &&
        facts.groups.map((group) => (
          <Group
            key={group.key}
            id={`group-${group.key}`}
            title={group.title}
            aside={waitingLabel(group.pending.length)}
            proposal
          >
            {group.pending.map((p) => (
              <FactRow
                key={p.id}
                proposal={p}
                document={document}
                open={open === p.id}
                onToggle={() => setChosen(open === p.id ? null : p.id)}
                onDecided={() => setChosen(nextPending(group.pending, p.id))}
                now={now}
              />
            ))}
          </Group>
        ))}

      {done && facts.known.length > 0 && (
        <Group id="group-known" title="Уже в карточке">
          {facts.known.map((fact) => {
            const view = knownView(fact);
            return (
              <Row
                key={`${fact.kind}-${view.title}`}
                icon={<Check aria-hidden className="size-5 text-ink-muted" />}
                title={view.title}
                subtitle={view.subtitle}
                aside={view.page === null ? undefined : `стр. ${view.page}`}
              />
            );
          })}
        </Group>
      )}

      {facts.decided.length > 0 && (
        <Group id="group-decided" title="Решённые">
          {facts.decided.map((p) => {
            const view = decidedView(p, me, now);
            return (
              <Row
                key={p.id}
                icon={
                  view.confirmed ? (
                    <CircleCheck aria-label="Подтверждено" className="size-5 text-success" />
                  ) : (
                    <X aria-label="Отклонено" className="size-5 text-ink-muted" />
                  )
                }
                title={view.title}
                subtitle={view.subtitle}
                aside={view.time}
              />
            );
          })}
        </Group>
      )}
    </>
  );
}

/** Документ ещё в работе или разбор не удался: фактов пока нет. */
function Pending({ document }: { document: DocumentOut }) {
  const text =
    document.status === "failed"
      ? "Факты не извлечены. Запустите разбор ещё раз кнопкой «Повторить» у документа."
      : "Факты появятся после разбора, список обновится сам.";
  return (
    <p className="rounded-xl bg-surface px-4 py-3 text-[15px] leading-[22px] text-ink-muted">
      {text}
    </p>
  );
}
