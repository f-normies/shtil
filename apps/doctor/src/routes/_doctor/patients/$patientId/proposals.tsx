import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { STATUS_TABS, statusOf } from "../../../../card/proposalQueue";
import { newestFirst } from "../../../../card/proposals";
import { usePatientProposals } from "../../../../card/queries";
import { ProposalsView } from "../../../../components/proposals/ProposalsView";
import { StatusTabs } from "../../../../components/proposals/QueueHeader";

type ProposalsSearch = { status?: "confirmed" | "rejected"; proposal?: string };

/**
 * Раздел «Предложения» карточки (спека #96, 3.5; кадр «07б»): тот же вид, что экран
 * «Предложения», без групп. Данные — `GET /patients/{id}/proposals`, статус — в адресе.
 */
export const Route = createFileRoute("/_doctor/patients/$patientId/proposals")({
  validateSearch: (search: Record<string, unknown>): ProposalsSearch => {
    const status = statusOf(search.status);
    const proposal = typeof search.proposal === "string" ? search.proposal : undefined;
    return { status: status === "proposed" ? undefined : status, proposal };
  },
  component: PatientProposals,
});

function PatientProposals() {
  const { patientId } = Route.useParams();
  const { user } = Route.useRouteContext();
  const search = Route.useSearch();
  const status = statusOf(search.status);
  const navigate = useNavigate({ from: Route.fullPath });
  const proposals = usePatientProposals(patientId);
  const items = newestFirst(proposals.data ?? []).filter((p) => p.status === status);

  return (
    <section className="flex flex-col gap-4">
      <Link
        className="text-sm text-primary underline"
        to="/patients/$patientId"
        params={{ patientId }}
      >
        ← К обзору
      </Link>
      <h2 className="text-lg font-semibold">Предложения</h2>
      <ProposalsView
        resetKey={status}
        initialId={search.proposal}
        variant="card"
        grouped={false}
        items={items}
        loading={proposals.isPending}
        failed={proposals.isError}
        empty={STATUS_TABS.find((t) => t.value === status)?.empty ?? ""}
        doctorId={user.id}
        header={
          <div className="px-2">
            <StatusTabs
              value={status}
              onChange={(next) =>
                navigate({ search: { status: next === "proposed" ? undefined : next } })
              }
            />
          </div>
        }
      />
    </section>
  );
}
