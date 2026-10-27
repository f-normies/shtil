import { SegmentedControl } from "@compass/ui";
import { Search } from "lucide-react";
import { progress, type QueueStatus, STATUS_TABS, statusOf } from "../../card/proposalQueue";

/** «Ждут решения | Подтверждены | Отклонены» (компонент «Врач v4/Сегментированный переключатель»). */
export function StatusTabs({
  value,
  onChange,
}: {
  value: QueueStatus;
  onChange: (status: QueueStatus) => void;
}) {
  return (
    <SegmentedControl
      ariaLabel="Статус предложений"
      className="w-full"
      options={STATUS_TABS.map(({ value: v, label }) => ({ value: v, label }))}
      value={value}
      onValueChange={(v) => onChange(statusOf(v))}
    />
  );
}

/**
 * Шапка списка на экране «Предложения» (кадр 04): число ждущих решения, поиск по ФИО,
 * статус и полоса «Разобрано сегодня N из M». Кнопки «подтвердить всё» нет: решение —
 * по каждому предложению отдельно.
 */
export function QueueHeader({
  status,
  onStatus,
  query,
  onQuery,
  summary,
}: {
  status: QueueStatus;
  onStatus: (status: QueueStatus) => void;
  query: string;
  onQuery: (query: string) => void;
  summary: { pending: number; decided_today: number } | undefined;
}) {
  const done = summary ? progress(summary) : null;
  return (
    <div className="flex flex-col gap-3 px-2">
      <header className="flex items-baseline justify-between gap-2 px-1">
        <h1 className="text-[26px] leading-8 font-semibold tracking-[-0.5px]">Предложения</h1>
        {summary && (
          <p className="text-[26px] leading-8 font-semibold text-ink-muted">
            <span className="sr-only">Ждут решения: </span>
            {summary.pending}
          </p>
        )}
      </header>
      <label className="flex items-center gap-2 rounded-lg bg-surface-sunken px-3 py-2 text-[15px]">
        <Search aria-hidden className="size-5 text-ink-muted" strokeWidth={1.75} />
        <span className="sr-only">Поиск по ФИО</span>
        <input
          type="search"
          className="w-full bg-transparent outline-none placeholder:text-ink-muted"
          placeholder="Поиск по ФИО"
          value={query}
          onChange={(e) => onQuery(e.target.value)}
        />
      </label>
      <StatusTabs value={status} onChange={onStatus} />
      {done && (
        <div className="flex flex-col gap-1.5 px-1 pt-1">
          <p className="flex justify-between text-sm">
            <span className="text-ink-muted">Разобрано сегодня</span>
            <span className="font-semibold text-ink">{done.text}</span>
          </p>
          <progress
            aria-label="Разобрано сегодня"
            aria-valuetext={done.text}
            max={Math.max(done.total, 1)}
            value={done.decided}
            className="h-1 w-full appearance-none overflow-hidden rounded-full bg-surface-sunken [&::-moz-progress-bar]:bg-primary [&::-webkit-progress-bar]:bg-surface-sunken [&::-webkit-progress-value]:rounded-full [&::-webkit-progress-value]:bg-primary"
          />
        </div>
      )}
    </div>
  );
}
