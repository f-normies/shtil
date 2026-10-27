import type { Schemas } from "@compass/api-client";
import { Link } from "@tanstack/react-router";
import { ChevronRight, CircleDotDashed } from "lucide-react";
import type { Day } from "../../card/days";
import { canOpenFinding, type DiaryEntry, findingView } from "../../card/diary";
import { DIVIDER, Group } from "../documents/Group";

/**
 * «Находки из записей» (кадр 06): предложения `freetext.finding` из дневника, ждущие
 * решения врача, при любом периоде. Группа в пунктире; «Разобрать» ведёт в «Предложения».
 * Запись находки — кнопка к строке в списке, если запись загружена или может быть на
 * следующих страницах (`canOpenFinding`). `entries` — запись каждой находки по её id;
 * `list` — что загружено из записей периода, `null` — списка записей нет.
 */
export function Findings({
  findings,
  entries,
  list,
  patientId,
  onOpenFinding,
}: {
  findings: Schemas["ProposalOut"][];
  entries: Map<string, DiaryEntry>;
  list: { hasNextPage: boolean; periodStart: Day } | null;
  patientId: string;
  onOpenFinding: (finding: Schemas["ProposalOut"]) => void;
}) {
  if (findings.length === 0) {
    return null;
  }
  return (
    <Group id="diary-findings" title="Находки из записей" proposal>
      {findings.map((f) => {
        const entry = entries.get(f.id);
        const view = findingView(f);
        const openable =
          list !== null && canOpenFinding(f, entry, list.hasNextPage, list.periodStart);
        return (
          <li key={f.id} className={`${DIVIDER} flex items-center gap-3 px-4 py-3`}>
            <CircleDotDashed aria-hidden className="size-5 shrink-0 text-primary" />
            <div className="min-w-0 flex-1">
              <p className="text-[15px] leading-[22px] text-ink">
                <span className="sr-only">Предложение системы: </span>
                {view.title}
              </p>
              <p className="text-[13px] leading-[18px] text-ink-muted">
                {view.category},{" "}
                {openable ? (
                  <button
                    type="button"
                    onClick={() => onOpenFinding(f)}
                    className="text-primary-text underline underline-offset-2 hover:text-primary-hover focus-visible:outline-2 focus-visible:outline-focus"
                  >
                    {view.record}
                  </button>
                ) : (
                  view.record
                )}
              </p>
            </div>
            <Link
              to="/patients/$patientId/proposals"
              params={{ patientId }}
              search={{ proposal: f.id }}
              hash={`proposal-${f.id}`}
              aria-label={`Разобрать: ${view.title}`}
              className="inline-flex h-9 shrink-0 items-center gap-1 rounded-md text-[15px] font-semibold text-primary-text hover:text-primary-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
            >
              Разобрать
              <ChevronRight aria-hidden className="size-5" />
            </Link>
          </li>
        );
      })}
    </Group>
  );
}
