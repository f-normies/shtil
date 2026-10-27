import { Button, SegmentedControl } from "@compass/ui";
import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { type Day, dayOf, rangeText, shortDate } from "../../../../card/days";
import {
  diaryFailure,
  diaryFindings,
  ENTRY_FILTERS,
  type EntryFilter,
  findingEntries,
  findingsByEntry,
  groupByDay,
} from "../../../../card/diary";
import { type DiaryQuery, useDiaryEntries, useDiaryMoods } from "../../../../card/diaryQueries";
import {
  diaryOpensOn,
  type Period,
  type PeriodSearch,
  parsePeriodSearch,
  periodQuery,
  resolvePeriod,
  type Slot,
  weekPeriod,
} from "../../../../card/mood";
import { usePatient, usePatientProposals } from "../../../../card/queries";
import { useOpenEntry } from "../../../../card/useOpenEntry";
import { EntriesSkeleton, EntryList, NoEntries } from "../../../../components/diary/EntryList";
import { Findings } from "../../../../components/diary/Findings";
import { MoodPanel } from "../../../../components/diary/MoodPanel";
import { Section } from "../../../../components/signals/Section";

/**
 * Вкладка «Дневник» (спека #115; кадры 06–06д) в карточке окна v4: настроение за период,
 * находки из записей, которые ждут решения, и записи. Период — в адресе: `?period=14` или
 * `?from=…&to=…`, поэтому «Назад» и обновление страницы его не теряют. Дни — по поясу
 * сервера (решение 3.2).
 */
export const Route = createFileRoute("/_doctor/patients/$patientId/diary")({
  validateSearch: parsePeriodSearch,
  component: Diary,
});

function Diary() {
  const { patientId } = Route.useParams();
  // Карточка уже загрузила пациента: пока его нет, загрузку и ошибку показывает она.
  const patient = usePatient(patientId);
  if (!patient.data) {
    return null;
  }
  return <DiaryTab patientId={patientId} observedSince={patient.data.created_at} />;
}

function DiaryTab({ patientId, observedSince }: { patientId: string; observedSince: string }) {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const today = dayOf(new Date());
  // Отдельного поля «начало наблюдения» нет: это дата создания пациента (решение 3.1).
  const observationStart = dayOf(observedSince);
  const period = resolvePeriod(search, today, observationStart);
  const query = periodQuery(period);
  const proposals = usePatientProposals(patientId);
  const findings = diaryFindings(proposals.data ?? []);

  if (!query) {
    // Меньше 7 дней наблюдения API дневника не отдаёт; находки из записей уже есть.
    return (
      <section aria-label="Дневник" className="flex flex-col gap-6">
        <Section title="Дневник" flush>
          <p role="status" className="px-4 py-5 text-[15px] leading-[22px] text-ink-muted">
            Дневник откроется {shortDate(diaryOpensOn(observationStart))}, с 7-го дня наблюдения.
          </p>
        </Section>
        <Findings
          findings={findings}
          entries={new Map()}
          list={null}
          patientId={patientId}
          onOpenFinding={() => {}}
        />
      </section>
    );
  }

  const setPeriod = (next: PeriodSearch) => navigate({ search: next });
  const showWeek = (slot: Slot) => {
    const week = weekPeriod(slot, today, observationStart);
    if (week) {
      setPeriod(week);
    }
  };
  return (
    <DiaryData
      patientId={patientId}
      period={period}
      query={query}
      today={today}
      observationStart={observationStart}
      findings={findings}
      onPeriod={setPeriod}
      onShowWeek={showWeek}
    />
  );
}

function DiaryData({
  patientId,
  period,
  query,
  today,
  observationStart,
  findings,
  onPeriod,
  onShowWeek,
}: {
  patientId: string;
  period: Period;
  query: DiaryQuery;
  today: Day;
  observationStart: Day;
  findings: ReturnType<typeof diaryFindings>;
  onPeriod: (search: PeriodSearch) => void;
  onShowWeek: (slot: Slot) => void;
}) {
  const [filter, setFilter] = useState<EntryFilter>("all");
  const moods = useDiaryMoods(patientId, query);
  const entries = useDiaryEntries(patientId, query);
  const opener = useOpenEntry(entries, `${query.start}/${query.end}`, () => setFilter("all"));

  const loaded = entries.data?.pages.flatMap((page) => page.items) ?? [];
  const periodText = rangeText(period.start, period.end);
  const failure = diaryFailure(entries.error, "Не удалось загрузить дневник");

  return (
    <section aria-label="Дневник" className="flex flex-col gap-6">
      <MoodPanel
        period={period}
        today={today}
        observationStart={observationStart}
        moods={moods}
        onPeriod={onPeriod}
        onOpenEntry={(entryId, at) =>
          void opener.open((list) => list.find((e) => e.entry_id === entryId), at)
        }
        onShowWeek={onShowWeek}
      />

      <Findings
        findings={findings}
        entries={findingEntries(findings, loaded)}
        list={{ hasNextPage: entries.hasNextPage, periodStart: period.start }}
        patientId={patientId}
        onOpenFinding={(f) => void opener.open((list) => findingEntries([f], list).get(f.id), null)}
      />

      <div className="flex items-center gap-4 pt-4">
        <div className="min-w-0 flex-1">
          <h2 className="text-xl leading-[26px] font-semibold text-ink">Записи</h2>
          <p className="text-[13px] leading-[18px] text-ink-muted">{periodText}</p>
        </div>
        <div data-forma="vrach" className="w-[384px] shrink-0">
          <SegmentedControl
            ariaLabel="Вид записей"
            className="w-full max-w-none"
            options={ENTRY_FILTERS}
            value={filter}
            onValueChange={(value) => setFilter(value as EntryFilter)}
          />
        </div>
      </div>
      <p role="status" className="px-4 text-sm text-ink empty:hidden">
        {opener.searching ? "Ищем запись…" : opener.unavailable ? "Запись недоступна" : ""}
      </p>

      {entries.isPending && <EntriesSkeleton />}
      {entries.isError && (
        <p role="alert" className="flex items-center gap-3 px-4 text-sm text-ink">
          {failure.text}
          {failure.retry && (
            <Button variant="secondary" onClick={() => void entries.refetch()}>
              Повторить
            </Button>
          )}
        </p>
      )}
      {entries.isSuccess && loaded.length === 0 && <NoEntries title={periodText} />}
      {loaded.length > 0 && (
        <EntryList
          groups={groupByDay(loaded, today, filter, entries.hasNextPage)}
          findings={findingsByEntry(findings, loaded)}
          patientId={patientId}
          highlighted={opener.highlighted}
        />
      )}
      {entries.hasNextPage && (
        <button
          type="button"
          disabled={entries.isFetchingNextPage}
          onClick={() => void entries.fetchNextPage()}
          className="self-start px-4 text-[15px] font-semibold text-primary-text hover:text-primary-hover disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-focus"
        >
          {entries.isFetchingNextPage ? "Загрузка…" : "Показать ещё записи"}
        </button>
      )}
    </section>
  );
}
