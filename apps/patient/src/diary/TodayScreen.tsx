// Kept as one screen because its week beads, mood prompts, survey state and cards share the same clock and local diary snapshot.
import type { Schemas } from "@compass/api-client";
import voiceIcon from "@compass/ui/assets/ikonka-golos.svg";
import microphoneIcon from "@compass/ui/assets/ikonka-mikrofon.svg";
import surveyIcon from "@compass/ui/assets/ikonka-opros.svg";
import textIcon from "@compass/ui/assets/ikonka-tekst.svg";
import timeIcon from "@compass/ui/assets/ikonka-vremya.svg";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import type { IntakeMark } from "../medications/doses";
import { IntakeInstructions, IntakeRow } from "../medications/IntakeRow";
import { doctorDisplayName, doctorInitials } from "../messaging/doctor-name";
import { useDoctorStatus, useUnreadMessageCount } from "../messaging/doctor-queries";
import { usePatientSyncState } from "../patient-sync-context";
import type { PatientSession } from "../session";
import { fetchDiaryEntries } from "./api";
import moodIcon from "./assets/mood-icon.svg";
import skyDay from "./assets/sky-day.svg";
import skyEvening from "./assets/sky-evening.svg";
import skyMorning from "./assets/sky-morning.svg";
import skyNight from "./assets/sky-night.svg";
import { intakeMarksByDose } from "./CheckInIntakeStep";
import { diaryWeekDays, diaryWeekRange, resolveDiaryClock } from "./clock";
import { MoodScale, moodLabel } from "./MoodScale";
import { mergeDiaryPage } from "./merge";
import { monthEntryCount } from "./month-entry-count";
import { PatientLogoutControl } from "./PatientLogoutControl";
import { PatientPage } from "./PatientPage";
import { QuickMoodSheet } from "./QuickMoodSheet";
import { getDiaryStore, loadIntakeView, useDiaryOverviewQueries } from "./queries";
import {
  capitalizeFirst,
  entriesCountLabel,
  entriesNoun,
  greetingName,
  monthInPrepositional,
} from "./russian-text";
import { resolveDiarySchedule } from "./survey-schedule";
import { syncAfterLocalWrite } from "./sync-after-write";
import type { DiaryEntryRecord, DiaryScheduleSnapshot, IntakeView } from "./types";
import { useDiaryLocal } from "./use-diary-local";

function ownerFor(session: PatientSession): string {
  return session.status === "online"
    ? session.user.id
    : session.status === "offline"
      ? session.ownerId
      : "";
}

function dateAtNoon(date: string): Date {
  return new Date(`${date}T12:00:00.000Z`);
}

function weekday(date: string): string {
  return new Intl.DateTimeFormat("ru-RU", { weekday: "short", timeZone: "UTC" })
    .format(dateAtNoon(date))
    .replace(".", "");
}

function weekdayInAccusative(date: string): string {
  return (
    [
      "в воскресенье",
      "в понедельник",
      "во вторник",
      "в среду",
      "в четверг",
      "в пятницу",
      "в субботу",
    ][dateAtNoon(date).getUTCDay()] ?? "в этот день"
  );
}

function pluralizedQuestionCount(count: number): string {
  const lastTwoDigits = count % 100;
  const lastDigit = count % 10;
  const noun =
    lastTwoDigits >= 11 && lastTwoDigits <= 14
      ? "вопросов"
      : lastDigit === 1
        ? "вопрос"
        : lastDigit >= 2 && lastDigit <= 4
          ? "вопроса"
          : "вопросов";
  return `${count} ${noun}`;
}

function surveyTitle(
  item: Schemas["ScaleDueOut"],
  definitions: readonly Schemas["ScaleDefinitionOut"][],
): string {
  const questionCount = definitions.find((definition) => definition.scale.id === item.scale_id)
    ?.scale.items.length;
  return questionCount
    ? `${item.scale_title}, ${pluralizedQuestionCount(questionCount)}`
    : item.scale_title;
}

function humanDate(occurredAt: string, timezone: string): string {
  return capitalizeFirst(
    new Intl.DateTimeFormat("ru-RU", {
      weekday: "long",
      day: "numeric",
      month: "long",
      timeZone: timezone,
    }).format(new Date(occurredAt)),
  );
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

function monthStartEnd(today: string): { start: string; end: string } {
  const [year, month] = today.split("-").map(Number);
  const end = new Date(Date.UTC(year ?? 0, month ?? 1, 0));
  return {
    start: `${today.slice(0, 7)}-01`,
    end: `${end.getUTCFullYear()}-${String(end.getUTCMonth() + 1).padStart(2, "0")}-${String(end.getUTCDate()).padStart(2, "0")}`,
  };
}

function MonthBeads({ entries }: { entries: readonly DiaryEntryRecord[] }) {
  return (
    <span className="patient-bead-row" aria-hidden="true">
      {entries
        .slice(0, 8)
        .reverse()
        .map((entry) => (
          <i key={entry.entry_id} data-level={entry.mood_level ?? 0} />
        ))}
    </span>
  );
}

function hourAt(timestamp: string, timezone: string): number {
  return Number(
    new Intl.DateTimeFormat("en", { hour: "2-digit", hourCycle: "h23", timeZone: timezone }).format(
      new Date(timestamp),
    ),
  );
}

function greeting(
  session: PatientSession,
  timezone: string,
  afterDaily: boolean,
  timestamp: string,
  displayName?: string,
): string {
  const hour = Number(
    new Intl.DateTimeFormat("en", { hour: "2-digit", hourCycle: "h23", timeZone: timezone }).format(
      new Date(timestamp),
    ),
  );
  const period =
    afterDaily || hour >= 23 || hour < 5
      ? "Добрый вечер,"
      : hour < 11
        ? "Доброе утро,"
        : hour < 17
          ? "Добрый день,"
          : "Добрый вечер,";
  const name = greetingName(
    displayName,
    session.status === "online" ? session.user.full_name : undefined,
  );
  return `${period}${name ? ` ${name}` : ""}`;
}

function activeSchedule(
  data: DiaryScheduleSnapshot | undefined,
  cache: DiaryScheduleSnapshot | null,
): DiaryScheduleSnapshot | null {
  return data ?? cache;
}

export function TodayScreen({
  session,
  displayName,
}: {
  session: PatientSession;
  displayName?: string;
}) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const overview = useDiaryOverviewQueries(session, queryClient);
  const local = useDiaryLocal(session);
  const { diarySyncNow } = usePatientSyncState();
  const doctorStatus = useDoctorStatus(session);
  const unreadCount = useUnreadMessageCount(session);
  const ownerId = ownerFor(session);
  const clock = resolveDiaryClock(overview.cache);
  const scheduleSnapshot = activeSchedule(
    overview.schedule.data,
    overview.cache?.schedule_snapshot ?? null,
  );
  const days = diaryWeekDays(clock.today);
  const monthRange = monthStartEnd(clock.today);
  const monthQuery = useQuery({
    queryKey: ["diary", ownerId, "month-entries", monthRange.start, monthRange.end],
    enabled: session.status === "online",
    staleTime: 30_000,
    retry: false,
    queryFn: () => fetchDiaryEntries({ ...monthRange, limit: 100 }),
  });
  const weekQuery = useQuery({
    queryKey: ["diary", ownerId, "week-entries", days[0], days[6]],
    enabled: session.status === "online",
    staleTime: 30_000,
    retry: false,
    queryFn: () => fetchDiaryEntries({ start: days[0], end: days[6], limit: 100 }),
  });
  const dailyEntryQuery = useQuery({
    queryKey: ["diary", ownerId, "daily-entry", clock.today],
    enabled: Boolean(ownerId),
    retry: false,
    networkMode: "always",
    queryFn: async () => (await getDiaryStore()).getDailyEntry(ownerId, clock.today),
  });
  const scheduleState = resolveDiarySchedule(
    scheduleSnapshot,
    clock.today,
    local.definitions.data?.map((item) => item.scale.id) ?? [],
    { latestRequestFailed: overview.schedule.isError },
  );
  const dailyEntry =
    local.entries.data?.find((entry) => entry.kind === "daily" && entry.daily_on === clock.today) ??
    dailyEntryQuery.data;
  const dailyVoiceCommand = local.outbox.data?.find(
    (command) => command.action === "voice" && command.entry_id === dailyEntry?.entry_id,
  );
  const dailyAudioDurationMs =
    dailyEntry?.audio?.duration_ms ??
    (dailyVoiceCommand?.action === "voice" ? dailyVoiceCommand.payload.duration_ms : undefined);
  const dailySaved = Boolean(
    dailyEntryQuery.data || dailyEntry || overview.context.data?.daily_status === "saved",
  );
  const unresolvedDailySurvey = local.drafts.data?.find(
    (draft) =>
      draft.mode === "daily" &&
      draft.step === "survey" &&
      (draft.survey_status === "unknown" || draft.survey_status === "pending") &&
      draft.daily_on === clock.today &&
      draft.entry_id === dailyEntry?.entry_id,
  );
  const pendingHideIds = new Set(
    local.outbox.data
      ?.filter((command) => command.action === "hide")
      .map((command) => command.entry_id) ?? [],
  );
  const reminderDate = dailySaved ? (days[3] ?? clock.today) : clock.today;
  const bannerGroups = new Map<string, Schemas["ScaleDueOut"][]>();
  for (const item of scheduleSnapshot?.items ?? []) {
    const isVisibleWeekDay =
      item.due_on >= (days[0] ?? clock.today) && item.due_on <= (days[6] ?? clock.today);
    const group = isVisibleWeekDay ? item.due_on : item.due_on <= clock.today ? "overdue" : null;
    if (group) bannerGroups.set(group, [...(bannerGroups.get(group) ?? []), item]);
  }
  const surveyBannerGroups = [...bannerGroups.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([dueOn, items]) => ({ dueOn, items }));
  const [quickMoodOpen, setQuickMoodOpen] = useState(false);
  const [quickMoodSaving, setQuickMoodSaving] = useState(false);
  const [quickMoodStatus, setQuickMoodStatus] = useState<string | null>(null);
  const prescriptions =
    overview.prescriptions.data ?? overview.cache?.prescriptions_snapshot ?? null;
  const message = overview.latestMessage.data ?? overview.cache?.latest_message ?? null;
  const messageKnown =
    Boolean(overview.cache?.latest_message_fetched_at) || overview.latestMessage.data !== undefined;
  const monthEntries = mergeDiaryPage(
    ownerId,
    local.entries.data ?? [],
    monthQuery.data?.items ?? [],
  ).filter(
    (entry) =>
      !entry.hidden &&
      !pendingHideIds.has(entry.entry_id) &&
      dateInTimezone(entry.occurred_at, clock.timezone).startsWith(clock.today.slice(0, 7)),
  );
  const count = monthEntryCount(
    monthEntries,
    clock.today,
    clock.timezone,
    overview.context.data?.month_entry_count ?? overview.cache?.context?.month_entry_count,
  );

  const intakeQuery = useQuery({
    queryKey: ["diary", ownerId, "intake", clock.today],
    enabled: Boolean(ownerId),
    retry: false,
    networkMode: "always",
    queryFn: () => loadIntakeView(ownerId, clock.today, session.status === "online"),
  });
  const intakeOutboxQuery = useQuery({
    queryKey: ["diary", ownerId, "intake-outbox"],
    enabled: Boolean(ownerId),
    retry: false,
    networkMode: "always",
    queryFn: async () => (await getDiaryStore()).getIntakeOutboxForOwner(ownerId),
  });
  const intakeMarks = intakeMarksByDose(
    intakeQuery.data?.items,
    intakeOutboxQuery.data,
    clock.today,
  );
  const takenCount = [...intakeMarks.values()].filter((mark) => mark.status === "taken").length;
  const totalDoses = prescriptions?.reduce((sum, item) => sum + item.times_per_day, 0) ?? 0;

  async function toggleDose(prescription: Schemas["ScheduleItem"], ordinal: number): Promise<void> {
    const key = `${prescription.prescription_id}:${ordinal}`;
    const status = intakeMarks.get(key)?.status === "taken" ? "unmarked" : "taken";
    try {
      await (await getDiaryStore()).enqueueIntakeMark({
        ownerId,
        payload: {
          operation_id: crypto.randomUUID(),
          prescription_id: prescription.prescription_id,
          scheduled_on: clock.today,
          dose_ordinal: ordinal,
          status,
          occurred_at: resolveDiaryClock(overview.cache).occurredAt,
        },
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["diary", ownerId, "intake", clock.today] }),
        queryClient.invalidateQueries({ queryKey: ["diary", ownerId, "intake-outbox"] }),
      ]);
    } catch {
      await queryClient.invalidateQueries({ queryKey: ["diary", ownerId, "intake-outbox"] });
    }
  }

  async function saveQuickMood(level: number): Promise<void> {
    setQuickMoodSaving(true);
    setQuickMoodStatus(null);
    try {
      const result = await (await getDiaryStore()).enqueueEntry({
        ownerId,
        payload: {
          entry_id: crypto.randomUUID(),
          operation_id: crypto.randomUUID(),
          kind: "mood",
          occurred_at: clock.occurredAt,
          daily_on: null,
          mood_level: level,
          mood_words: [],
          text: null,
          other_medicine_text: null,
        },
      });
      if (result.status === "conflict" || result.status === "daily-exists") {
        throw new Error("Не удалось сохранить отметку настроения.");
      }
      await Promise.all([
        local.refresh(),
        queryClient.invalidateQueries({ queryKey: ["diary", ownerId, "month-entries"] }),
      ]);
      const time = new Intl.DateTimeFormat("ru-RU", {
        hour: "2-digit",
        minute: "2-digit",
        timeZone: clock.timezone,
      }).format(new Date(clock.occurredAt));
      setQuickMoodStatus(`Записано в ${time}`);
    } catch {
      setQuickMoodStatus("Не удалось сохранить отметку. Попробуйте ещё раз.");
    } finally {
      setQuickMoodSaving(false);
    }
  }

  const weekEntries = mergeDiaryPage(
    ownerId,
    local.entries.data ?? [],
    weekQuery.data?.items ?? [],
  ).filter((entry) => !entry.hidden && !pendingHideIds.has(entry.entry_id));
  const entriesByDay = new Map<string, DiaryEntryRecord[]>();
  for (const entry of weekEntries) {
    const date = entry.daily_on ?? dateInTimezone(entry.occurred_at, clock.timezone);
    entriesByDay.set(date, [...(entriesByDay.get(date) ?? []), entry]);
  }
  const hour = hourAt(clock.occurredAt, clock.timezone);
  const sky =
    dailySaved || hour >= 23 || hour < 5
      ? skyNight
      : hour >= 17
        ? skyEvening
        : hour >= 11
          ? skyDay
          : skyMorning;
  const doctorMessageCard = (
    <DoctorMessageCard
      message={message}
      known={messageKnown}
      stale={
        overview.latestMessage.data === undefined &&
        Boolean(overview.cache?.latest_message_fetched_at)
      }
      doctorName={doctorStatus.data?.peer_full_name ?? ""}
      unread={unreadCount > 0}
      today={clock.today}
      timezone={clock.timezone}
    />
  );
  const medicationCard = (
    <MedicationCard
      prescriptions={prescriptions}
      marks={intakeMarks}
      totalDoses={totalDoses}
      takenCount={takenCount}
      source={intakeQuery.data?.source ?? "empty"}
      onToggle={toggleDose}
    />
  );

  return (
    <PatientPage className="patient-today" session={session}>
      <header
        className="patient-sky"
        data-sky={
          dailySaved || hour >= 23 || hour < 5
            ? "night"
            : hour >= 17
              ? "evening"
              : hour >= 11
                ? "day"
                : "morning"
        }
      >
        <img className="patient-sky__scene" src={sky} alt="" aria-hidden="true" />
        <p className="patient-sky__date">{humanDate(clock.occurredAt, clock.timezone)}</p>
        <h1 className="type-patient-greeting">
          {greeting(session, clock.timezone, dailySaved, clock.occurredAt, displayName)}
        </h1>
      </header>
      <main className="patient-today__content">
        {session.status === "offline" && (
          <p className="patient-offline-note" role="status">
            {session.cause === "server"
              ? "Сервер недоступен. Сохранённые записи останутся на устройстве."
              : "Офлайн-режим. Записи останутся на устройстве до восстановления связи."}
          </p>
        )}
        {dailySaved ? (
          <section
            className="patient-card patient-daily-summary"
            aria-labelledby="daily-summary-title"
          >
            <div className="patient-daily-summary__heading">
              <span className="patient-check-mark" aria-hidden="true">
                ✓
              </span>
              <h2 id="daily-summary-title">Отметка сделана</h2>
              <time>
                {dailyEntry
                  ? new Intl.DateTimeFormat("ru-RU", {
                      hour: "2-digit",
                      minute: "2-digit",
                      timeZone: clock.timezone,
                    }).format(new Date(dailyEntry.occurred_at))
                  : ""}
              </time>
            </div>
            <div className="patient-summary-pills">
              {dailyEntry?.mood_level && <span>{moodLabel(dailyEntry.mood_level)}</span>}
              {dailyAudioDurationMs !== undefined && (
                <span>
                  <img src={microphoneIcon} alt="" /> {Math.floor(dailyAudioDurationMs / 60_000)}:
                  {String(Math.floor((dailyAudioDurationMs % 60_000) / 1_000)).padStart(2, "0")}
                </span>
              )}
              {totalDoses > 0 && (
                <span>
                  Приём лекарств: {takenCount} из {totalDoses}
                </span>
              )}
            </div>
            {unresolvedDailySurvey && (
              <div className="patient-survey-continuation">
                <p role="status">Опрос не завершён</p>
                <Link to="/check-in" search={{ mood: undefined }}>
                  Продолжить опрос
                </Link>
              </div>
            )}
            <div className="patient-month-count">
              <strong>{count}</strong> {entriesNoun(count)} {monthInPrepositional(clock.today)}
              <MonthBeads entries={monthEntries} />
            </div>
          </section>
        ) : (
          <section
            className="patient-card patient-checkin-prompt"
            aria-labelledby="home-mood-title"
          >
            <p className="patient-overline">
              <img src={timeIcon} alt="" /> Вечерняя отметка в 21:00
            </p>
            <h2 id="home-mood-title" className="type-patient-question">
              Как вы сейчас?
            </h2>
            <MoodScale
              context="home-daily"
              selected={null}
              onSelect={(level) => void navigate({ to: "/check-in", search: { mood: level } })}
            />
            <div className="patient-rule" />
            <p>Начните с цвета. Дальше — пара слов о дне и лекарства, около минуты.</p>
          </section>
        )}

        <section className="patient-card patient-quick-card" aria-labelledby="quick-title">
          {dailySaved ? <p className="patient-overline">Быстрая запись</p> : null}
          <h2 id="quick-title" className={dailySaved ? "type-patient-question" : undefined}>
            {dailySaved ? "Что-то изменилось?" : "Быстрая запись"}
          </h2>
          {dailySaved ? (
            <p>Нажмите на цвет — запись сохранится сразу.</p>
          ) : (
            <p>В любой момент, сколько угодно раз.</p>
          )}
          {!dailySaved ? (
            <div className="patient-quick-tiles">
              <button type="button" onClick={() => setQuickMoodOpen(true)}>
                <span>
                  <img src={moodIcon} alt="" />
                </span>
                Настроение
              </button>
              <Link to="/diary/new/voice">
                <span>
                  <img src={microphoneIcon} alt="" />
                </span>
                Голосом
              </Link>
              <Link to="/diary/new/text">
                <span>
                  <img src={textIcon} alt="" />
                </span>
                Текстом
              </Link>
            </div>
          ) : (
            <>
              <MoodScale
                context="home-quick"
                selected={null}
                disabled={quickMoodSaving}
                onSelect={(level) => void saveQuickMood(level)}
              />
              <p className="patient-quick-feedback" role="status" aria-live="polite">
                {quickMoodStatus ?? ""}
                {quickMoodSaving ? "Сохраняем на устройстве…" : ""}
              </p>
              <div className="patient-quick-pills">
                <Link to="/diary/new/voice">
                  <img src={voiceIcon} alt="" />
                  Голосом
                </Link>
                <Link to="/diary/new/text">
                  <img src={textIcon} alt="" />
                  Текстом
                </Link>
              </div>
            </>
          )}
        </section>

        <section className="patient-card patient-week-card" aria-labelledby="week-title">
          <div className="patient-card__heading">
            <h2 id="week-title">Неделя</h2>
            <span>{diaryWeekRange(clock.today)}</span>
          </div>
          <ol className="patient-week-strip" aria-label="Дни недели">
            {days.map((date) => {
              const isToday = date === clock.today;
              const hasReminder = date === reminderDate;
              const entries = entriesByDay.get(date) ?? [];
              const surveyDue =
                scheduleSnapshot?.items.some((item) => item.due_on === date) ?? false;
              const hasKnownNoEntry =
                !isToday &&
                date < clock.today &&
                weekQuery.data !== undefined &&
                entries.length === 0;
              const recordsQualifier = entries.length
                ? `, ${entriesCountLabel(entries.length)}`
                : hasKnownNoEntry
                  ? ", записей нет"
                  : "";
              return (
                <li
                  className="patient-week-day"
                  data-today={isToday || undefined}
                  data-survey-due={surveyDue || undefined}
                  data-reminder={hasReminder || undefined}
                  key={date}
                  aria-label={`${weekday(date)} ${dateAtNoon(date).getUTCDate()}${recordsQualifier}${hasReminder ? ", напоминание в 21:00" : ""}${surveyDue ? ", опрос" : ""}`}
                >
                  <span className="patient-week-day__label">{weekday(date)}</span>
                  <strong className="patient-week-day__date" data-today={isToday || undefined}>
                    {dateAtNoon(date).getUTCDate()}
                  </strong>
                  <span className="patient-week-day__dots" aria-hidden="true">
                    {entries.slice(0, 3).map((entry) => (
                      <i key={entry.entry_id} data-level={entry.mood_level ?? 0} />
                    ))}
                    {hasKnownNoEntry && <i data-missed="true" />}
                    {hasReminder && (
                      <span className="patient-week-day__reminder">
                        <img src={timeIcon} alt="" />
                        <small>21:00</small>
                      </span>
                    )}
                    {surveyDue && (
                      <span className="patient-week-day__survey">
                        <img src={surveyIcon} alt="" />
                        <small>опрос</small>
                      </span>
                    )}
                  </span>
                </li>
              );
            })}
          </ol>
          {scheduleState.status === "unknown" && (
            <p className="patient-schedule-note">
              Расписание опросов пока не подтверждено.
              {scheduleState.needsRefresh ? " Обновим после подключения." : ""}
            </p>
          )}
          {weekQuery.isError && (
            <p className="patient-data-note" role="status">
              Не удалось обновить неделю. Показаны записи с этого устройства.
            </p>
          )}
          {surveyBannerGroups.map(({ dueOn, items }) => (
            <div className="patient-survey-banner" key={dueOn}>
              <img src={surveyIcon} alt="" />
              <span>
                {dueOn === "overdue"
                  ? "Опросы к заполнению: "
                  : dueOn > clock.today
                    ? `${weekdayInAccusative(dueOn)} после отметки — `
                    : "Опросы уже доступны: "}
                {items.map((item) => surveyTitle(item, local.definitions.data ?? [])).join("; ")}.
              </span>
            </div>
          ))}
        </section>

        {dailySaved ? (
          <>
            {doctorMessageCard}
            {medicationCard}
          </>
        ) : (
          <>
            {medicationCard}
            {doctorMessageCard}
          </>
        )}
        <Link to="/help" className="patient-help-link">
          Если очень тяжело
        </Link>
        <PatientLogoutControl session={session} />
        <Link className="consent-revoke-link" to="/consent/revoke">
          Отозвать согласие на обработку данных
        </Link>
      </main>
      <QuickMoodSheet
        open={quickMoodOpen}
        ownerId={ownerId}
        cache={overview.cache}
        onClose={() => setQuickMoodOpen(false)}
        onSaved={() => {
          void local.refresh();
          void syncAfterLocalWrite({ syncNow: diarySyncNow, refreshLocal: local.refresh });
        }}
      />
    </PatientPage>
  );
}

function MedicationCard({
  prescriptions,
  marks,
  totalDoses,
  takenCount,
  source,
  onToggle,
}: {
  prescriptions: Schemas["ScheduleItem"][] | null;
  marks: Map<string, IntakeMark>;
  totalDoses: number;
  takenCount: number;
  source: IntakeView["source"];
  onToggle: (prescription: Schemas["ScheduleItem"], ordinal: number) => void;
}) {
  return (
    <section className="patient-card patient-medication-card" aria-labelledby="meds-title">
      <div className="patient-card__heading">
        <h2 id="meds-title">Лекарства сегодня</h2>
        {totalDoses > 0 && (
          <span>
            Принято {takenCount} из {totalDoses}
          </span>
        )}
      </div>
      {prescriptions === null ? (
        <p className="patient-muted">
          Схема приёма недоступна. Показываем только сохранённые данные.
        </p>
      ) : prescriptions.length === 0 ? (
        <p className="patient-empty-medication">Действующих назначений на сегодня нет.</p>
      ) : (
        <>
          <div className="patient-dose-list">
            {prescriptions.map((prescription) =>
              Array.from({ length: prescription.times_per_day }, (_, index) => {
                const ordinal = index + 1;
                const key = `${prescription.prescription_id}:${ordinal}`;
                return (
                  <IntakeRow
                    key={key}
                    prescription={prescription}
                    ordinal={ordinal}
                    mark={marks.get(key)}
                    onToggle={() => onToggle(prescription, ordinal)}
                  />
                );
              }),
            )}
          </div>
          <IntakeInstructions prescriptions={prescriptions} />
        </>
      )}
      {source === "cache" && (
        <p className="patient-data-note">Показаны последние сохранённые отметки.</p>
      )}
      {source === "unavailable" && (
        <p className="patient-data-note">Отметки пока недоступны. Дневник можно заполнить.</p>
      )}
    </section>
  );
}

function messageTimeLabel(createdAt: string, today: string, timezone: string): string {
  const date = new Date(createdAt);
  const day = dateInTimezone(createdAt, timezone);
  const time = new Intl.DateTimeFormat("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: timezone,
  }).format(date);
  if (day === today) return `сегодня в ${time}`;
  const dayLabel = new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "long",
    timeZone: timezone,
  }).format(date);
  return `${dayLabel} в ${time}`;
}

function DoctorMessageCard({
  message,
  known,
  stale,
  doctorName,
  unread,
  today,
  timezone,
}: {
  message: Schemas["MessageOut"] | null;
  known: boolean;
  stale: boolean;
  doctorName: string;
  unread: boolean;
  today: string;
  timezone: string;
}) {
  const initials = doctorInitials(doctorName);
  return (
    <Link
      to="/chat"
      className="patient-card patient-message-card"
      aria-label={`${doctorDisplayName(doctorName)}: ${
        message?.text ?? message?.transcription_text ?? "сообщения врача"
      }${unread ? ". Есть новое сообщение" : ""}`}
    >
      <div className="patient-message-card__head">
        <span className="patient-avatar" aria-hidden="true">
          {initials || "Вр"}
        </span>
        <div className="patient-message-card__who">
          <h2>{doctorDisplayName(doctorName)}</h2>
          <small>
            {message ? `Врач, ${messageTimeLabel(message.created_at, today, timezone)}` : "Врач"}
          </small>
        </div>
        {unread && <span className="patient-message-card__badge">Новое</span>}
      </div>
      {message?.text || message?.transcription_text ? (
        <p>{message.text ?? message.transcription_text}</p>
      ) : message?.kind === "voice" ? (
        <p>
          Голосовое сообщение{" "}
          {message.transcription_status === "done" ? "расшифровано" : "расшифровывается"}.
        </p>
      ) : known ? (
        <p>Сообщений от врача пока нет. Здесь появится последнее.</p>
      ) : (
        <p>Сообщения врача появятся здесь, когда будут доступны.</p>
      )}
      {stale && (
        <small className="patient-data-note">Показано последнее сохранённое сообщение.</small>
      )}
    </Link>
  );
}
