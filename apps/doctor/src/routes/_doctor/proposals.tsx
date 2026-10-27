import { Button } from "@compass/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { STATUS_TABS, statusOf } from "../../card/proposalQueue";
import { useProposalQueue, useProposalSummary } from "../../card/queries";
import { ProposalsView } from "../../components/proposals/ProposalsView";
import { QueueHeader } from "../../components/proposals/QueueHeader";

type ProposalsSearch = { status?: "confirmed" | "rejected" };

// Поиск уходит на сервер, когда врач перестал печатать.
const SEARCH_DELAY_MS = 300;

/**
 * Экран «Предложения» (спека #96, 3.5; кадр «Врач v4 · 04»): предложения всех пациентов
 * врача по статусу, группы по пациентам, поиск по ФИО, подробности справа. Статус —
 * в адресе (`?status=confirmed|rejected`), «Ждут решения» — без параметра.
 */
export const Route = createFileRoute("/_doctor/proposals")({
  validateSearch: (search: Record<string, unknown>): ProposalsSearch => {
    const status = statusOf(search.status);
    return { status: status === "proposed" ? undefined : status };
  },
  component: ProposalsScreen,
});

function ProposalsScreen() {
  const { user } = Route.useRouteContext();
  const status = statusOf(Route.useSearch().status);
  const navigate = useNavigate({ from: Route.fullPath });
  const [query, setQuery] = useState("");
  const q = useDebounced(query.trim(), SEARCH_DELAY_MS);
  const queue = useProposalQueue(status, q);
  const summary = useProposalSummary();
  const items = queue.data?.pages.flatMap((page) => page.items) ?? [];
  const tab = STATUS_TABS.find((t) => t.value === status);
  const empty = q ? `Нет предложений по запросу «${q}»` : (tab?.empty ?? "");

  return (
    <ProposalsView
      resetKey={status}
      variant="screen"
      grouped
      items={items}
      loading={queue.isPending}
      failed={queue.isError}
      empty={empty}
      doctorId={user.id}
      header={
        <QueueHeader
          status={status}
          onStatus={(next) =>
            navigate({ search: { status: next === "proposed" ? undefined : next } })
          }
          query={query}
          onQuery={setQuery}
          summary={summary.data}
        />
      }
      footer={
        queue.hasNextPage && (
          <div className="px-2 pt-2">
            <Button
              variant="secondary"
              className="w-full"
              disabled={queue.isFetchingNextPage}
              onClick={() => void queue.fetchNextPage()}
            >
              {queue.isFetchingNextPage ? "Загружаем…" : "Показать ещё"}
            </Button>
          </div>
        )
      }
    />
  );
}

/** Значение, которое не менялось `delay` мс. */
function useDebounced<T>(value: T, delay: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return settled;
}
