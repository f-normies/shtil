import type { Schemas } from "@compass/api-client";
import type { DayGroup } from "../../card/diary";
import { Group } from "../documents/Group";
import { EntryItem } from "./EntryItem";

/**
 * Записи за период по дням, новые выше (кадр 06). Фильтр по видам уже применён: день без
 * записей нужного вида остаётся с пометкой. Удалённых записей API не отдаёт.
 */
export function EntryList({
  groups,
  findings,
  patientId,
  highlighted,
}: {
  groups: DayGroup[];
  findings: Map<string, Schemas["ProposalOut"][]>;
  patientId: string;
  highlighted: string | null;
}) {
  return (
    <div className="flex flex-col gap-6">
      {groups.map((group) => (
        <Group key={group.day} id={`diary-day-${group.day}`} title={group.title}>
          {group.entries.length === 0 ? (
            <li className="px-4 py-3 text-sm text-ink-muted">Записей этого вида нет</li>
          ) : (
            group.entries.map((entry) => (
              <EntryItem
                key={entry.entry_id}
                entry={entry}
                findings={findings.get(entry.entry_id) ?? []}
                patientId={patientId}
                highlighted={entry.entry_id === highlighted}
              />
            ))
          )}
        </Group>
      ))}
    </div>
  );
}

/** Пустой период (кадр 06д): подпись группы — период, внутри — «За эти дни записей нет». */
export function NoEntries({ title }: { title: string }) {
  return (
    <Group id="diary-no-entries" title={title}>
      <li className="px-4 py-5 text-center text-[15px] leading-[22px] text-ink-muted">
        За эти дни записей нет
      </li>
    </Group>
  );
}

/** Скелетон трёх записей, пока список загружается. */
export function EntriesSkeleton() {
  return (
    <div aria-hidden className="flex flex-col gap-2">
      <span className="ml-4 h-4 w-40 rounded bg-surface-sunken motion-safe:animate-pulse" />
      <div className="flex flex-col gap-5 rounded-xl bg-surface px-4 py-4">
        {[0, 1, 2].map((i) => (
          <div key={i} className="flex gap-3">
            <span className="size-5 rounded-full bg-surface-sunken motion-safe:animate-pulse" />
            <div className="flex flex-1 flex-col gap-2">
              <span className="h-4 w-44 rounded bg-surface-sunken motion-safe:animate-pulse" />
              <span className="h-4 w-72 rounded bg-surface-sunken motion-safe:animate-pulse" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
