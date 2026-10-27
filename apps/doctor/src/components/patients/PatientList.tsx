import { SegmentedControl, StatusIcon } from "@compass/ui";
import { Info, Search } from "lucide-react";
import { type KeyboardEvent, useRef, useState } from "react";
import { whenText } from "../../card/alerts";
import { nextIndex } from "../../card/listNav";
import { matchesName, type QueueKind, type QueueRow } from "../../card/queue";
import { useFindShortcut } from "../../card/useFindShortcut";
import { ListSkeleton } from "./ListSkeleton";

export type Filter = "all" | "signals";

const FILTERS = [
  { value: "all", label: "Все" },
  { value: "signals", label: "С сигналами" },
] as const;

const ICON: Partial<Record<QueueKind, "required" | "advised" | "proposed">> = {
  mandatory: "required",
  desirable: "advised",
  proposal: "proposed",
};
const SPOKEN: Partial<Record<QueueKind, string>> = {
  mandatory: "обязательно",
  desirable: "желательно",
  proposal: "предложение системы",
};

/**
 * Список «Пациенты» 360 px (спека #13, раздел 10.2; кадр «Врач v4 · 02»): поиск по ФИО,
 * отбор, группы «Требуют внимания» и «Остальные». Список — одна остановка Tab: ↑↓, Home
 * и End меняют выбор, Enter и двойной клик открывают карточку.
 */
export function PatientList({
  attention,
  rest,
  selectedId,
  onSelect,
  onOpen,
  onInvite,
  loading,
  failed,
  onRetry,
}: {
  attention: QueueRow[];
  rest: QueueRow[];
  selectedId: string | undefined;
  onSelect: (patientId: string) => void;
  onOpen: (patientId: string) => void;
  onInvite: () => void;
  loading: boolean;
  failed: boolean;
  onRetry: () => void;
}) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const searchRef = useRef<HTMLInputElement>(null);
  useFindShortcut(searchRef);
  const shown = (rows: QueueRow[]) =>
    rows.filter(
      (r) => matchesName(r.patient.full_name, query) && (filter === "all" || r.alerts.length > 0),
    );
  const groups = [
    { title: "Требуют внимания", rows: shown(attention) },
    { title: "Остальные", rows: shown(rest) },
  ].filter((g) => g.rows.length > 0);
  const order = groups.flatMap((g) => g.rows.map((r) => r.patient.id));
  // Выбранный пациент может быть скрыт отбором или поиском: тогда активной строки нет.
  const active = selectedId && order.includes(selectedId) ? selectedId : undefined;
  const total = attention.length + rest.length;
  const now = new Date();

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const at = active ? order.indexOf(active) : -1;
    const next = nextIndex(e.key, at, order.length);
    if (next !== null && order[next]) {
      e.preventDefault();
      onSelect(order[next]);
      document.getElementById(optionId(order[next]))?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter" && active) {
      e.preventDefault();
      onOpen(active);
    }
  };

  return (
    <aside className="flex h-full w-[360px] shrink-0 flex-col gap-3 bg-surface px-2 pt-6">
      <header className="flex items-baseline justify-between px-2">
        <h1 className="text-[26px] leading-8 font-semibold tracking-[-0.5px]">Пациенты</h1>
        <span className="text-[26px] leading-8 font-semibold text-ink-subtle">{total}</span>
      </header>
      <label className="mx-2 flex h-9 items-center gap-2 rounded-lg bg-surface-sunken px-3 text-sm">
        <Search aria-hidden className="size-4 text-ink-muted" />
        <span className="sr-only">Поиск по ФИО</span>
        <input
          ref={searchRef}
          type="search"
          className="w-full bg-transparent outline-none placeholder:text-ink-muted"
          placeholder="Поиск по ФИО"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Escape" && setQuery("")}
        />
      </label>
      <SegmentedControl
        ariaLabel="Отбор пациентов"
        className="mx-2 w-auto"
        options={FILTERS}
        value={filter}
        onValueChange={(v) => setFilter(v as Filter)}
      />

      <div className="-mx-2 flex min-h-0 flex-1 flex-col overflow-y-auto px-2 pb-4">
        {loading && <ListSkeleton label="Загружаем пациентов…" />}
        {failed && (
          <p className="flex items-center justify-between px-2 text-sm text-danger">
            Не удалось загрузить пациентов.
            <button type="button" className="font-semibold text-primary-text" onClick={onRetry}>
              Повторить
            </button>
          </p>
        )}
        {!loading && !failed && total > 0 && order.length === 0 && (
          <div className="flex flex-col items-start gap-1 px-2 text-sm text-ink-muted">
            {query.trim() ? `Никого не нашли по «${query.trim()}»` : "Нет пациентов с сигналами"}
            {query.trim() && (
              <button
                type="button"
                className="h-9 font-semibold text-primary-text hover:underline"
                onClick={() => setQuery("")}
              >
                Сбросить поиск
              </button>
            )}
          </div>
        )}
        {order.length > 0 && (
          <div
            role="listbox"
            tabIndex={0}
            aria-label="Пациенты"
            aria-activedescendant={active ? optionId(active) : undefined}
            onKeyDown={onKeyDown}
            className="flex flex-col rounded-lg focus-visible:outline-2 focus-visible:outline-focus"
          >
            {groups.map((g) => (
              // biome-ignore lint/a11y/useSemanticElements: группа строк listbox, fieldset тут не подходит.
              <div key={g.title} role="group" aria-labelledby={groupId(g.title)}>
                <p
                  id={groupId(g.title)}
                  className="flex justify-between px-3 pt-4 pb-2 text-xs font-semibold tracking-[0.4px] text-ink-muted uppercase"
                >
                  <span>{g.title}</span>
                  <span>{g.rows.length}</span>
                </p>
                {g.rows.map((r) => (
                  <Row
                    key={r.patient.id}
                    row={r}
                    selected={r.patient.id === selectedId}
                    now={now}
                    onSelect={() => onSelect(r.patient.id)}
                    onOpen={() => onOpen(r.patient.id)}
                  />
                ))}
              </div>
            ))}
          </div>
        )}
        <button
          type="button"
          onClick={onInvite}
          className="mt-4 h-9 self-start px-3 text-sm font-semibold text-primary-text hover:underline"
        >
          Пригласить пациента
        </button>
      </div>
    </aside>
  );
}

function Row({
  row,
  selected,
  now,
  onSelect,
  onOpen,
}: {
  row: QueueRow;
  selected: boolean;
  now: Date;
  onSelect: () => void;
  onOpen: () => void;
}) {
  const icon = ICON[row.kind];
  // Отзыв согласия (спека #112): вторую строку даёт очередь, значок — здесь.
  const revoked = row.kind === "none" && Boolean(row.patient.consent_revoked_at);
  const time = row.at ? whenText(row.at, now) : "";
  const spoken = [row.patient.full_name, SPOKEN[row.kind], row.reason, time]
    .filter(Boolean)
    .join(", ");
  const outline =
    row.kind === "proposal" && !selected
      ? "outline-1 -outline-offset-1 outline-dashed outline-primary-text"
      : "";
  return (
    // biome-ignore lint/a11y/useKeyWithClickEvents: клавиатура — у списка (aria-activedescendant).
    <div
      id={optionId(row.patient.id)}
      role="option"
      aria-selected={selected}
      tabIndex={-1}
      aria-label={spoken}
      title={row.patient.full_name}
      onClick={onSelect}
      onDoubleClick={onOpen}
      className={`flex cursor-default gap-3 rounded-lg px-3 py-2.5 transition-colors duration-150 motion-reduce:transition-none ${selected ? "bg-primary text-primary-ink" : `hover:bg-surface-sunken ${outline}`}`}
    >
      {/* Слово уровня — подсказкой у значка: в строке нет места для подписи (кадр 02). */}
      <span className="flex size-5 shrink-0 items-center pt-0.5" title={SPOKEN[row.kind]}>
        {icon && <StatusIcon status={icon} decorative accent={selected} />}
        {revoked && <Info aria-hidden className="size-5" />}
      </span>
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="flex justify-between gap-2">
          <span className="truncate text-[15px] leading-5 font-semibold">
            {row.patient.full_name}
          </span>
          <span className={`shrink-0 text-xs leading-5 ${selected ? "" : "text-ink-muted"}`}>
            {time}
          </span>
        </span>
        <span className={`truncate text-[13px] leading-[18px] ${selected ? "" : "text-ink"}`}>
          {row.reason}
        </span>
      </span>
    </div>
  );
}

function optionId(patientId: string): string {
  return `patient-${patientId}`;
}

function groupId(title: string): string {
  return `group-${title === "Остальные" ? "rest" : "attention"}`;
}
