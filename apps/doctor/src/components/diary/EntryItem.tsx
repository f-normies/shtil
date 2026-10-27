import type { Schemas } from "@compass/api-client";
import { Link } from "@tanstack/react-router";
import { ChartSpline, Check, CircleDotDashed, Clock, Info, Mic, Pencil } from "lucide-react";
import { type ReactNode, useState } from "react";
import { timeOf } from "../../card/days";
import {
  audioUrl,
  type DiaryEntry,
  type EntryKind,
  entryAnchor,
  entryRows,
  findingLabel,
  isLongText,
  KIND_TITLES,
  ownText,
  transcriptView,
} from "../../card/diary";
import { DIVIDER } from "../documents/Group";
import { AudioPlayer } from "./AudioPlayer";

const ICONS: Record<EntryKind, typeof Mic> = {
  voice: Mic,
  text: Pencil,
  mood: ChartSpline,
  daily: Check,
};

/**
 * Запись дневника (кадр 06): значок вида, название, время; ниже — плеер и расшифровка,
 * текст, строки отметки и ссылки на находки по записи. `highlighted` — запись только что
 * открыли из графика или находки: фон коротко подсвечен.
 */
export function EntryItem({
  entry,
  findings,
  patientId,
  highlighted = false,
}: {
  entry: DiaryEntry;
  findings: Schemas["ProposalOut"][];
  patientId: string;
  highlighted?: boolean;
}) {
  const Icon = ICONS[entry.kind];
  const transcript = entry.audio ? transcriptView(entry.audio) : null;
  const rows = entryRows(entry);
  const text = ownText(entry);

  return (
    <li
      id={entryAnchor(entry.entry_id)}
      tabIndex={-1}
      className={`${DIVIDER} flex gap-3 px-4 py-3 transition-colors duration-700 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-focus ${highlighted ? "bg-primary-soft" : ""}`}
    >
      <Icon aria-hidden className="mt-0.5 size-5 shrink-0 text-ink-muted" />
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <div className="flex items-center gap-3">
          <p className="flex-1 text-[15px] leading-[22px] text-ink">{KIND_TITLES[entry.kind]}</p>
          <time
            dateTime={entry.occurred_at}
            className="shrink-0 text-[13px] leading-[18px] text-ink-subtle"
          >
            {timeOf(entry.occurred_at)}
          </time>
        </div>
        {entry.audio && (
          <AudioPlayer
            url={audioUrl(patientId, entry.entry_id)}
            durationMs={entry.audio.duration_ms}
          />
        )}
        {transcript && (
          // Смена «Расшифровывается…» на текст по событию `diary.changed` слышна читалке.
          <div aria-live="polite">
            {transcript.spoken ? (
              <Quote className="motion-safe:animate-fade-in">{transcript.text}</Quote>
            ) : (
              <p className="flex items-center gap-1.5 text-[13px] leading-[18px] text-ink-muted">
                {transcript.status === "processing" ? (
                  <Clock aria-hidden className="size-4" />
                ) : (
                  <Info aria-hidden className="size-4" />
                )}
                {transcript.text}
              </p>
            )}
          </div>
        )}
        {rows.length > 0 && (
          <dl className="flex flex-col gap-1 text-sm leading-5">
            {rows.map((row) => (
              <div key={row.label} className="flex gap-2">
                <dt className="w-[140px] shrink-0 text-ink-muted">{row.label}</dt>
                <dd className="min-w-0 flex-1 text-ink">{row.value}</dd>
              </div>
            ))}
          </dl>
        )}
        {text && <Quote>{text}</Quote>}
        {findings.map((f) => (
          <Link
            key={f.id}
            to="/patients/$patientId/proposals"
            params={{ patientId }}
            search={{ proposal: f.id }}
            hash={`proposal-${f.id}`}
            className="inline-flex items-center gap-1.5 self-start rounded-md border border-dashed border-primary-text py-1 pr-2.5 pl-1.5 text-[13px] leading-[18px] text-primary-text hover:bg-primary-soft focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
          >
            <CircleDotDashed aria-hidden className="size-5" />
            {findingLabel(f)}
          </Link>
        ))}
      </div>
    </li>
  );
}

/** «Цитата пациента»: слова пациента, их можно выделить и скопировать. Длинные — до 6 строк. */
function Quote({ children, className = "" }: { children: string; className?: string }) {
  const [full, setFull] = useState(false);
  const long = isLongText(children);
  return (
    <div className={`flex max-w-[420px] flex-col items-start gap-1 ${className}`}>
      <blockquote
        className={`rounded-[14px] rounded-bl-[4px] bg-surface-sunken px-3.5 py-2.5 text-[15px] leading-[22px] break-words text-ink ${long && !full ? "line-clamp-6" : ""}`}
      >
        {children}
      </blockquote>
      {long && (
        <ShowMore expanded={full} onToggle={() => setFull((v) => !v)}>
          {full ? "Свернуть" : "Показать полностью"}
        </ShowMore>
      )}
    </div>
  );
}

function ShowMore({
  expanded,
  onToggle,
  children,
}: {
  expanded: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-expanded={expanded}
      onClick={onToggle}
      className="text-sm font-semibold text-primary-text hover:text-primary-hover focus-visible:outline-2 focus-visible:outline-focus"
    >
      {children}
    </button>
  );
}
