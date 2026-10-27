import { Button } from "@compass/ui";
import {
  AudioLines,
  BookOpen,
  CalendarDays,
  ClipboardList,
  FileText,
  MessageSquare,
  NotebookPen,
  Pill,
  TriangleAlert,
  UserRound,
} from "lucide-react";
import type { ReactNode } from "react";
import { formatDateTime } from "../../card/format";
import {
  type ApplicabilityIcon,
  type ApplicabilityRow,
  applicability,
  findingSource,
  type QueueItem,
  rowView,
  rulesText,
} from "../../card/proposalQueue";
import { FINDING } from "../../card/proposals";
import { Section } from "../signals/Section";
import { StatusGlyph } from "./ProposalList";

const ICONS: Record<ApplicabilityIcon, typeof Pill> = {
  drug: Pill,
  calendar: CalendarDays,
  indication: ClipboardList,
  profile: UserRound,
  document: FileText,
};

/**
 * «Источник» (кадр 04): у находки — тип и время записи; у предложений из разбора — версия
 * правил разбора из `ai_runs`. Источник назначения и сигнала — строка «Источник правила»
 * в самом предложении (`SourceLine`, инспектор #106).
 */
export function SourceSection({ item }: { item: QueueItem }) {
  const source = item.kind === FINDING ? findingSource(item) : null;
  const rules = rulesText(item);
  if (!source && !rules) {
    return null;
  }
  const RecordIcon = source?.voice
    ? AudioLines
    : item.payload.source_type === "message"
      ? MessageSquare
      : NotebookPen;
  return (
    <Section title="Источник">
      <ul className="flex flex-col divide-y divide-separator">
        {source && (
          <FactLine
            icon={<RecordIcon aria-hidden className={ICON} strokeWidth={1.75} />}
            text={source.record}
            detail={source.note}
            aside={
              <time dateTime={source.at} className="text-[13px] text-ink-muted">
                {formatDateTime(source.at)}
              </time>
            }
          />
        )}
        {rules && (
          <FactLine
            icon={<BookOpen aria-hidden className={ICON} strokeWidth={1.75} />}
            text="Версия правил разбора"
            detail={rules}
          />
        )}
      </ul>
    </Section>
  );
}

/** «Применимость»: факты из `payload`; всё, что требует внимания, — значком и словами. */
export function ApplicabilitySection({ item }: { item: QueueItem }) {
  const rows = applicability(item);
  if (rows.length === 0) {
    return null;
  }
  return (
    <Section title="Применимость">
      <ul className="flex flex-col divide-y divide-separator">
        {rows.map((row) => (
          <ApplicabilityLine key={`${row.icon}-${row.text}`} row={row} />
        ))}
      </ul>
    </Section>
  );
}

/** «Связанные предложения»: «Открыть» выбирает связанное в списке. */
export function RelatedSection({
  related,
  onOpen,
}: {
  related: QueueItem[];
  onOpen: (id: string) => void;
}) {
  if (related.length === 0) {
    return null;
  }
  return (
    <Section title={related.length === 1 ? "Связанное предложение" : "Связанные предложения"}>
      <ul className="flex flex-col gap-2">
        {related.map((r) => {
          const view = rowView(r);
          return (
            <li
              key={r.id}
              className={`flex items-start gap-3 rounded-lg p-3 ${r.status === "proposed" ? "border border-dashed border-primary-text" : "border border-line"}`}
            >
              <StatusGlyph status={r.status} />
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="text-[15px] leading-[22px] text-ink">{view.title}</span>
                {view.detail && (
                  <span className="text-[13px] leading-[18px] text-ink-muted">{view.detail}</span>
                )}
              </span>
              <Button
                variant="link"
                aria-label={`Открыть: ${view.title}`}
                onClick={() => onOpen(r.id)}
              >
                Открыть
              </Button>
            </li>
          );
        })}
      </ul>
    </Section>
  );
}

const ICON = "mt-0.5 size-5 shrink-0 text-ink-subtle";

function ApplicabilityLine({ row }: { row: ApplicabilityRow }) {
  const Icon = ICONS[row.icon];
  return (
    <FactLine
      icon={<Icon aria-hidden className={ICON} strokeWidth={1.75} />}
      text={row.text}
      detail={
        row.detail &&
        (row.warning ? (
          <span className="flex items-center gap-1.5 font-medium text-warning">
            <TriangleAlert aria-hidden className="size-4 shrink-0" />
            {row.detail}
          </span>
        ) : (
          row.detail
        ))
      }
    />
  );
}

function FactLine({
  icon,
  text,
  detail,
  aside,
}: {
  icon: ReactNode;
  text: string;
  detail: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <li className="flex gap-3 py-3 first:pt-0 last:pb-0">
      {icon}
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="text-[15px] leading-[22px] text-ink">{text}</span>
        {detail && <span className="text-[13px] leading-[18px] text-ink-muted">{detail}</span>}
      </span>
      {aside && <span className="shrink-0">{aside}</span>}
    </li>
  );
}
