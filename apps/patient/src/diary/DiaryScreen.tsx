import { Button } from "@compass/ui";
import calendarIcon from "@compass/ui/assets/ikonka-kalendar.svg";
import microphoneIcon from "@compass/ui/assets/ikonka-mikrofon.svg";
import textIcon from "@compass/ui/assets/ikonka-tekst.svg";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import { usePatientSyncState } from "../patient-sync-context";
import type { PatientSession } from "../session";
import { fetchDiaryEntries } from "./api";
import moodIcon from "./assets/mood-icon.svg";
import { resolveDiaryClock } from "./clock";
import { diaryEntryStatusLabel } from "./entry-status";
import { moodLabel } from "./MoodScale";
import { mergeDiaryPage } from "./merge";
import { monthEntryCount } from "./month-entry-count";
import { PatientPage } from "./PatientPage";
import { QuickMoodSheet } from "./QuickMoodSheet";
import { getDiaryStore, useDiaryOverviewQueries } from "./queries";
import { entriesCountLabel, entriesNoun, monthInPrepositional } from "./russian-text";
import { syncAfterLocalWrite } from "./sync-after-write";
import { useDiaryLocal } from "./use-diary-local";

type Filter = "all" | "mood" | "records";
type DiaryEntry = ReturnType<typeof mergeDiaryPage>[number];
const WAVE_BARS = Array.from({ length: 24 }, (_, slot) => ({
  id: `bar-${slot}`,
  height: 6 + ((slot * 11 + 7) % 17),
}));

function ownerFor(session: PatientSession): string {
  return session.status === "online"
    ? session.user.id
    : session.status === "offline"
      ? session.ownerId
      : "";
}

function dateInTimezone(timestamp: string, timezone: string): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date(timestamp));
  const part = (key: string) => parts.find((item) => item.type === key)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")}`;
}

function timeLabel(timestamp: string, timezone: string): string {
  return new Intl.DateTimeFormat("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: timezone,
  }).format(new Date(timestamp));
}

function dayLabel(date: string, today: string, timezone: string): string {
  if (date === today) return "Сегодня";
  const yesterday = new Date(`${today}T12:00:00Z`);
  yesterday.setUTCDate(yesterday.getUTCDate() - 1);
  const prior = `${yesterday.getUTCFullYear()}-${String(yesterday.getUTCMonth() + 1).padStart(2, "0")}-${String(yesterday.getUTCDate()).padStart(2, "0")}`;
  if (date === prior) return "Вчера";
  return new Intl.DateTimeFormat("ru-RU", { weekday: "long", timeZone: timezone }).format(
    new Date(`${date}T12:00:00Z`),
  );
}

function recordType(entry: DiaryEntry): string {
  if (entry.kind === "daily") return "Ежедневная отметка";
  if (entry.kind === "mood") return "Отметка настроения";
  if (entry.kind === "voice")
    return `Голосовая${entry.audio ? `, ${Math.floor(entry.audio.duration_ms / 60_000)}:${String(Math.floor((entry.audio.duration_ms % 60_000) / 1_000)).padStart(2, "0")}` : ""}`;
  return "Заметка";
}

function entryDescription(entry: DiaryEntry): string {
  if (entry.text) return entry.text;
  if (entry.audio?.transcript) return entry.audio.transcript;
  if (entry.kind === "voice") return "Голосовая запись";
  if (entry.kind === "daily") return "Ежедневная отметка";
  return "";
}

function DiaryCard({
  entry,
  timezone,
  ownerId,
  online,
  onHidden,
}: {
  entry: DiaryEntry;
  timezone: string;
  ownerId: string;
  online: boolean;
  onHidden: () => Promise<void>;
}) {
  const label = moodLabel(entry.mood_level);
  const statusText = diaryEntryStatusLabel(entry, online);
  const startRef = useRef<{ x: number; y: number; pointerId: number } | null>(null);
  const hideDialogRef = useRef<HTMLDialogElement>(null);
  const suppressClickRef = useRef(false);
  const [confirmHide, setConfirmHide] = useState(false);
  const [hiding, setHiding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const dialog = hideDialogRef.current;
    if (!dialog) return;
    if (confirmHide && !dialog.open) dialog.showModal();
    if (!confirmHide && dialog.open) dialog.close();
  }, [confirmHide]);

  function handlePointerDown(event: React.PointerEvent<HTMLAnchorElement>): void {
    if (event.pointerType !== "touch") return;
    startRef.current = { x: event.clientX, y: event.clientY, pointerId: event.pointerId };
  }

  function handlePointerUp(event: React.PointerEvent<HTMLAnchorElement>): void {
    const start = startRef.current;
    startRef.current = null;
    if (!start || event.pointerType !== "touch" || start.pointerId !== event.pointerId) return;
    const deltaX = event.clientX - start.x;
    const deltaY = event.clientY - start.y;
    if (deltaX > -72 || Math.abs(deltaX) <= Math.abs(deltaY) * 1.3) return;
    event.preventDefault();
    suppressClickRef.current = true;
    window.setTimeout(() => {
      suppressClickRef.current = false;
    }, 350);
    setError(null);
    setConfirmHide(true);
  }

  async function hideEntry(): Promise<void> {
    setHiding(true);
    setError(null);
    try {
      const result = await (await getDiaryStore()).enqueueHide({
        ownerId,
        entryId: entry.entry_id,
        payload: { operation_id: crypto.randomUUID(), expected_revision: entry.revision },
      });
      if (result.status === "conflict")
        throw new Error("Запись изменилась. Обновите дневник и попробуйте ещё раз.");
      setConfirmHide(false);
      await onHidden();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось скрыть запись.");
    } finally {
      setHiding(false);
    }
  }

  return (
    <>
      <Link
        className="patient-diary-entry"
        to="/diary/$entryId"
        params={{ entryId: entry.entry_id }}
        aria-label={`${label ? `${label}. ` : ""}${recordType(entry)}. ${entryDescription(entry)}`}
        onPointerDown={handlePointerDown}
        onPointerUp={handlePointerUp}
        onPointerCancel={() => {
          startRef.current = null;
        }}
        onClick={(event) => {
          if (!suppressClickRef.current) return;
          event.preventDefault();
          event.stopPropagation();
        }}
      >
        <span
          className={`patient-diary-entry__badge patient-diary-entry__badge--${entry.kind}`}
          aria-hidden="true"
        >
          {entry.kind === "mood" ? (
            <img src={moodIcon} alt="" />
          ) : entry.kind === "voice" ? (
            <img src={microphoneIcon} alt="" />
          ) : entry.kind === "text" ? (
            <img src={textIcon} alt="" />
          ) : (
            <span>✓</span>
          )}
        </span>
        <span className="patient-diary-entry__content">
          <span className="patient-diary-entry__head">
            <strong>{label ?? recordType(entry)}</strong>
            <time>{timeLabel(entry.occurred_at, timezone)}</time>
          </span>
          {label && <span className="patient-diary-entry__type">{recordType(entry)}</span>}
          {entry.kind === "voice" && entry.audio?.status !== "failed" && (
            <span className="patient-diary-wave" role="img" aria-label="Голосовая запись">
              <span className="patient-diary-wave__play" aria-hidden="true">
                ▶
              </span>
              <span className="patient-diary-wave__bars" aria-hidden="true">
                {WAVE_BARS.map(({ id, height }) => (
                  <i key={id} style={{ height: `${height}px` }} />
                ))}
              </span>
            </span>
          )}
          {entryDescription(entry) && (
            <span className="patient-diary-entry__description">{entryDescription(entry)}</span>
          )}
          {entry.kind === "daily" && entry.mood_words.length > 0 && (
            <span className="patient-diary-entry__row">
              <span className="patient-diary-entry__row-label">Слова</span>
              <span className="patient-summary-pills">
                {entry.mood_words.map((word) => (
                  <span key={word}>{word}</span>
                ))}
              </span>
            </span>
          )}
          {statusText && (
            <span className="patient-diary-status" data-state={entry.sync_state}>
              {statusText}
            </span>
          )}
        </span>
      </Link>
      <dialog
        ref={hideDialogRef}
        className="patient-confirm-dialog"
        role="alertdialog"
        aria-labelledby={`diary-hide-title-${entry.entry_id}`}
        onCancel={(event) => {
          event.preventDefault();
          setConfirmHide(false);
        }}
        onClose={() => setConfirmHide(false)}
      >
        <h2 id={`diary-hide-title-${entry.entry_id}`} className="type-patient-title">
          Удалить запись?
        </h2>
        <p className="patient-body-small">
          Она пропадёт из дневника. Медицинская запись останется в истории.
        </p>
        {entry.sync_state === "pending" && (
          <p className="patient-offline-note">Скроется у врача, когда появится сеть.</p>
        )}
        {error && (
          <p role="alert" className="patient-data-note">
            {error}
          </p>
        )}
        <div className="patient-confirm-dialog__actions">
          <Button variant="secondary" disabled={hiding} onClick={() => setConfirmHide(false)}>
            Оставить
          </Button>
          <Button loading={hiding} onClick={() => void hideEntry()}>
            Удалить
          </Button>
        </div>
      </dialog>
    </>
  );
}

/** Owner-bound diary list with local-first records, server refresh and stable-ID deduplication. */
export function DiaryScreen({ session }: { session: PatientSession }) {
  const queryClient = useQueryClient();
  const overview = useDiaryOverviewQueries(session, queryClient);
  const local = useDiaryLocal(session);
  const { diarySyncNow } = usePatientSyncState();
  const ownerId = ownerFor(session);
  const online = session.status === "online";
  const clock = resolveDiaryClock(overview.cache);
  const [filter, setFilter] = useState<Filter>("all");
  const [selectedDay, setSelectedDay] = useState("");
  const [moodOpen, setMoodOpen] = useState(false);
  const server = useInfiniteQuery({
    queryKey: ["diary", ownerId, "server-entries", selectedDay],
    initialPageParam: undefined as string | undefined,
    enabled: session.status === "online",
    staleTime: 30_000,
    retry: false,
    queryFn: ({ pageParam }) =>
      fetchDiaryEntries({
        filter: "all",
        ...(selectedDay ? { start: selectedDay, end: selectedDay } : {}),
        ...(pageParam ? { cursor: pageParam } : {}),
        limit: 20,
      }),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });
  const pendingHideIds = new Set(
    (local.outbox.data ?? []).filter((item) => item.action === "hide").map((item) => item.entry_id),
  );
  const serverEntries = (server.data?.pages.flatMap((page) => page.items) ?? []).filter(
    (entry) => !pendingHideIds.has(entry.entry_id),
  );
  const merged = useMemo(
    () => mergeDiaryPage(ownerId, local.entries.data ?? [], serverEntries),
    [local.entries.data, ownerId, serverEntries],
  );
  const monthEntries = merged.filter(
    (entry) => !entry.hidden && entry.occurred_at.startsWith(clock.today.slice(0, 7)),
  );
  const visible = merged.filter((entry) => {
    if (entry.hidden) return false;
    if (selectedDay && dateInTimezone(entry.occurred_at, clock.timezone) !== selectedDay)
      return false;
    if (filter === "mood") return entry.kind === "mood" || entry.kind === "daily";
    if (filter === "records") return entry.kind === "text" || entry.kind === "voice";
    return true;
  });
  const grouped = new Map<string, DiaryEntry[]>();
  for (const entry of visible) {
    const date = dateInTimezone(entry.occurred_at, clock.timezone);
    grouped.set(date, [...(grouped.get(date) ?? []), entry]);
  }
  const localOnly = session.status !== "online" || server.isError;
  const dayCounts = new Map<string, number>();
  for (const entry of merged) {
    const day = dateInTimezone(entry.occurred_at, clock.timezone);
    dayCounts.set(day, (dayCounts.get(day) ?? 0) + 1);
  }
  const monthCount = monthEntryCount(
    merged,
    clock.today,
    clock.timezone,
    overview.context.data?.month_entry_count ?? overview.cache?.context?.month_entry_count,
  );
  const month = monthInPrepositional(clock.today);
  const loading = visible.length === 0 && (local.entries.isPending || server.isPending);

  function setFilterValue(value: Filter): void {
    setFilter(value);
  }

  return (
    <PatientPage className="patient-diary" session={session}>
      <main className="patient-diary__content">
        <header className="patient-diary__header">
          <h1 className="type-patient-greeting">Дневник</h1>
          <label className="patient-calendar-button" aria-label="Выбрать день в дневнике">
            <img src={calendarIcon} alt="" />
            <input
              type="date"
              value={selectedDay}
              max={clock.today}
              onChange={(event) => {
                const value = event.currentTarget.value;
                setSelectedDay(value > clock.today ? clock.today : value);
              }}
              aria-label="Выбрать дату"
            />
          </label>
        </header>
        <section className="patient-diary-quick" aria-labelledby="diary-quick-title">
          <h2 id="diary-quick-title" className="type-patient-title">
            Как вы сейчас?
          </h2>
          <div className="patient-quick-tiles">
            <button type="button" onClick={() => setMoodOpen(true)}>
              <span>
                <img src={moodIcon} alt="" />
              </span>
              Настроение
            </button>
            <Link to="/diary/new/voice">
              <span>
                <img src={microphoneIcon} alt="" />
              </span>
              Голос
            </Link>
            <Link to="/diary/new/text">
              <span>
                <img src={textIcon} alt="" />
              </span>
              Текст
            </Link>
          </div>
        </section>
        <fieldset className="patient-diary-filter">
          <legend className="sr-only">Фильтр дневника</legend>
          {(
            [
              ["all", "Все"],
              ["mood", "Настроение"],
              ["records", "Записи"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              aria-pressed={filter === value}
              data-active={filter === value || undefined}
              onClick={() => setFilterValue(value)}
            >
              {label}
            </button>
          ))}
        </fieldset>
        {localOnly && (
          <p role="status" className="patient-offline-note">
            {server.isError
              ? "Не удалось обновить дневник. Показаны сохранённые записи."
              : "Офлайн-режим. Показаны записи на этом устройстве."}
          </p>
        )}
        <section className="patient-diary-groups" aria-label="Записи дневника">
          {loading && (
            <p className="patient-screen-loading" role="status">
              Загружаем дневник…
            </p>
          )}
          {[...grouped.entries()].map(([date, entries]) => (
            <section className="patient-diary-day" key={date}>
              <header>
                <h2>{dayLabel(date, clock.today, clock.timezone)}</h2>
                <time>
                  {new Intl.DateTimeFormat("ru-RU", {
                    day: "numeric",
                    month: "long",
                    timeZone: clock.timezone,
                  }).format(new Date(`${date}T12:00:00Z`))}
                </time>
              </header>
              {entries.map((entry) => (
                <DiaryCard
                  key={entry.entry_id}
                  entry={entry}
                  timezone={clock.timezone}
                  ownerId={ownerId}
                  online={online}
                  onHidden={async () => {
                    await Promise.all([
                      local.refresh(),
                      queryClient.invalidateQueries({
                        queryKey: ["diary", ownerId, "server-entries"],
                      }),
                    ]);
                    void syncAfterLocalWrite({
                      syncNow: diarySyncNow,
                      refreshLocal: local.refresh,
                    });
                  }}
                />
              ))}
            </section>
          ))}
          {visible.length === 0 && !loading && (
            <div className="patient-empty-card">
              <h2>Здесь пока пусто</h2>
              <p>Новые записи появятся в дневнике после сохранения.</p>
            </div>
          )}
        </section>
        {server.hasNextPage && (
          <button
            type="button"
            className="patient-diary-more"
            disabled={server.isFetchingNextPage}
            onClick={() => void server.fetchNextPage()}
          >
            {server.isFetchingNextPage ? "Загружаем…" : "Показать ещё"}
          </button>
        )}
        <section
          className="patient-month-count"
          aria-label={
            monthCount === undefined
              ? `Количество записей ${month} неизвестно`
              : `${entriesCountLabel(monthCount)} ${month}`
          }
        >
          <strong>{monthCount ?? "—"}</strong> {entriesNoun(monthCount ?? 0)} {month}
          <span className="patient-bead-row" aria-hidden="true">
            {monthEntries
              .slice(0, 12)
              .reverse()
              .map((entry) => (
                <i key={entry.entry_id} data-level={entry.mood_level ?? 0} />
              ))}
          </span>
        </section>
      </main>
      <QuickMoodSheet
        open={moodOpen}
        ownerId={ownerId}
        cache={overview.cache}
        onClose={() => setMoodOpen(false)}
        onSaved={() => {
          void local.refresh();
          void queryClient.invalidateQueries({ queryKey: ["diary", ownerId, "server-entries"] });
          void syncAfterLocalWrite({ syncNow: diarySyncNow, refreshLocal: local.refresh });
        }}
      />
    </PatientPage>
  );
}
