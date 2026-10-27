import type { Schemas } from "@compass/api-client";
import { Link, useNavigate } from "@tanstack/react-router";
import { Search } from "lucide-react";
import { type KeyboardEvent, useRef, useState } from "react";
import { byPriority, openGroups, stateLine, whenText } from "../../card/alerts";
import { deputyNote } from "../../card/escalation";
import { nextIndex } from "../../card/listNav";
import { useFindShortcut } from "../../card/useFindShortcut";
import { LEVEL_ICON } from "../card/LevelBadge";

type Alert = Schemas["AlertOut"];
export type Tab = "open" | "resolved";
export type SignalsSearch = { tab?: Tab; patient?: string };

const LEVEL_TONE = { mandatory: "text-danger", desirable: "text-warning" } as const;
const LEVEL_WORD = { mandatory: "Обязательно", desirable: "Желательно" } as const;

/**
 * Список сигналов 360 px (кадр «Врач v4 · 03»): поиск по ФИО, «Открытые | Решённые»,
 * группы «Новые» и «В работе у вас». Строка открывает подробности справа. Список — одна
 * остановка Tab (строка выбранного сигнала), ↑↓, Home и End переводят выбор и фокус.
 */
export function SignalList({
  alerts,
  names,
  search,
  selectedId,
  loading,
  failed,
}: {
  alerts: Alert[];
  names: Map<string, string>;
  search: SignalsSearch;
  selectedId?: string;
  loading: boolean;
  failed: boolean;
}) {
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);
  useFindShortcut(searchRef);
  const navigate = useNavigate();
  const needle = query.trim().toLowerCase();
  const shown = alerts.filter(
    (a) => !needle || (names.get(a.patient_id) ?? "").toLowerCase().includes(needle),
  );
  const tab = search.tab ?? "open";
  const groups =
    tab === "open"
      ? (() => {
          const { fresh, inWork } = openGroups(shown);
          return [
            { title: "Новые", items: fresh },
            { title: "В работе у вас", items: inWork },
          ];
        })()
      : [{ title: "Решённые", items: [...shown].sort(byPriority) }];
  const now = new Date();
  const order = groups.flatMap((g) => g.items.map((a) => a.id));
  // Остановка Tab — выбранный сигнал, иначе первая строка.
  const tabStop = selectedId && order.includes(selectedId) ? selectedId : order[0];
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const current = (e.target as HTMLElement).dataset.alertId;
    const next = nextIndex(e.key, current ? order.indexOf(current) : -1, order.length);
    const id = next !== null ? order[next] : undefined;
    if (!id) return;
    e.preventDefault();
    void navigate({ to: "/alerts/$alertId", params: { alertId: id }, search });
    document.getElementById(rowId(id))?.focus();
  };

  return (
    <aside className="flex w-[360px] shrink-0 flex-col gap-3 overflow-y-auto border-r border-line bg-surface px-2 py-4">
      <header className="flex items-baseline justify-between px-2">
        <h1 className="text-2xl font-semibold">Сигналы</h1>
        <span className="text-2xl font-semibold text-ink-muted">{alerts.length}</span>
      </header>
      <label className="mx-2 flex items-center gap-2 rounded-lg bg-canvas px-3 py-2 text-sm">
        <Search aria-hidden className="size-4 text-ink-muted" />
        <span className="sr-only">Поиск по ФИО</span>
        <input
          ref={searchRef}
          type="search"
          className="w-full bg-transparent outline-none placeholder:text-ink-muted"
          placeholder="Поиск по ФИО"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </label>
      <nav
        aria-label="Сигналы"
        className="mx-2 grid grid-cols-2 rounded-lg bg-canvas p-0.5 text-sm"
      >
        {(["open", "resolved"] as const).map((t) => (
          <Link
            key={t}
            to="/alerts"
            search={{ ...search, tab: t }}
            aria-current={tab === t ? "page" : undefined}
            className={`rounded-md py-1.5 text-center ${tab === t ? "bg-surface font-medium shadow-sm" : "text-ink-muted"}`}
          >
            {t === "open" ? "Открытые" : "Решённые"}
          </Link>
        ))}
      </nav>
      {loading && <p className="px-2 text-sm text-ink-muted">Загружаем сигналы…</p>}
      {failed && <p className="px-2 text-sm text-danger">Не удалось загрузить сигналы.</p>}
      {!loading && !failed && shown.length === 0 && (
        <p className="px-2 text-sm text-ink-muted">
          {tab === "open" ? "Открытых сигналов нет" : "Решённых сигналов нет"}
        </p>
      )}
      {/* biome-ignore lint/a11y/noStaticElementInteractions: клавиатура строк-ссылок, фокус — на самой ссылке */}
      <div className="flex flex-col gap-3" onKeyDown={onKeyDown}>
        {groups
          .filter((g) => g.items.length > 0)
          .map((g) => (
            <section key={g.title} aria-label={g.title} className="flex flex-col">
              <h2 className="flex justify-between px-3 py-2 text-xs font-semibold tracking-wide text-ink-muted uppercase">
                <span>{g.title}</span>
                <span>{g.items.length}</span>
              </h2>
              <ul className="flex flex-col">
                {g.items.map((a) => (
                  <li key={a.id}>
                    <SignalRow
                      alert={a}
                      name={names.get(a.patient_id) ?? "Пациент"}
                      search={search}
                      selected={a.id === selectedId}
                      tabStop={a.id === tabStop}
                      now={now}
                    />
                  </li>
                ))}
              </ul>
            </section>
          ))}
      </div>
    </aside>
  );
}

function SignalRow({
  alert,
  name,
  search,
  selected,
  tabStop,
  now,
}: {
  alert: Alert;
  name: string;
  search: SignalsSearch;
  selected: boolean;
  tabStop: boolean;
  now: Date;
}) {
  const Icon = LEVEL_ICON[alert.level];
  return (
    <Link
      id={rowId(alert.id)}
      data-alert-id={alert.id}
      tabIndex={tabStop ? 0 : -1}
      to="/alerts/$alertId"
      params={{ alertId: alert.id }}
      search={search}
      aria-current={selected ? "true" : undefined}
      className={`flex gap-3 rounded-lg px-3 py-2.5 ${selected ? "bg-primary text-primary-ink" : "hover:bg-canvas"}`}
    >
      <Icon
        aria-hidden
        className={`mt-0.5 size-5 shrink-0 ${selected ? "" : LEVEL_TONE[alert.level]}`}
      />
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="flex justify-between gap-2">
          <span className="truncate font-semibold">{name}</span>
          <span className={`shrink-0 text-xs ${selected ? "" : "text-ink-muted"}`}>
            {whenText(alert.last_triggered_at, now)}
          </span>
        </span>
        <span className="line-clamp-2 text-sm">{alert.title}</span>
        <span className={`text-xs ${selected ? "" : "text-ink-muted"}`}>
          {/* Уровень словом: категории различимы не только цветом и формой значка. */}
          {LEVEL_WORD[alert.level]} · {stateLine(alert, now)}
        </span>
        {alert.as_deputy && <span className="text-xs font-semibold">{deputyNote(alert)}</span>}
      </span>
    </Link>
  );
}

function rowId(alertId: string): string {
  return `signal-${alertId}`;
}
