import { Button } from "@compass/ui";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { selectedDocument } from "../../../../card/documents";
import { useDocuments, usePatientProposals } from "../../../../card/queries";
import { DocumentFacts } from "../../../../components/documents/DocumentFacts";
import { DocumentList } from "../../../../components/documents/DocumentList";
import { UploadDocument } from "../../../../components/documents/UploadDocument";

/**
 * История болезни (спека #30, раздел 6; кадр 07в): загрузка, документы и найденные в них
 * факты. Отдельного раздела в карточке нет: экран открывается из обзора и по ссылке
 * у предложений `ingestion.*`. `?document=` — какой документ открыт.
 */
export const Route = createFileRoute("/_doctor/patients/$patientId/documents")({
  validateSearch: (search: Record<string, unknown>): { document?: string } =>
    typeof search.document === "string" ? { document: search.document } : {},
  component: CaseHistory,
});

function CaseHistory() {
  const { patientId } = Route.useParams();
  const search = Route.useSearch();
  const { user } = Route.useRouteContext();
  const navigate = useNavigate({ from: Route.fullPath });
  const documents = useDocuments(patientId);
  const proposals = usePatientProposals(patientId);
  const list = documents.data ?? [];
  const selected = selectedDocument(list, search.document);
  const select = (id: string) => navigate({ search: { document: id }, replace: true });
  // Пересчитывается при каждом ответе опроса: «дольше обычного» и «сегодня» не устаревают.
  const now = new Date();

  return (
    <section
      aria-labelledby="case-history"
      className="mx-auto flex w-full max-w-[640px] flex-col gap-6 pb-12"
    >
      <div className="flex flex-col gap-1 px-4">
        <Link
          className="self-start text-sm text-primary underline"
          to="/patients/$patientId"
          params={{ patientId }}
        >
          ← К обзору
        </Link>
        <h2 id="case-history" className="text-lg font-semibold text-ink">
          История болезни
        </h2>
      </div>

      <UploadDocument patientId={patientId} onUploaded={select} />

      {documents.isPending && <Skeleton />}
      {documents.isError && (
        <div className="flex flex-wrap items-center gap-3 px-4">
          <p role="alert" className="text-sm text-danger">
            Ошибка: не удалось загрузить документы.
          </p>
          <Button variant="secondary" onClick={() => documents.refetch()}>
            Повторить
          </Button>
        </div>
      )}
      {documents.isSuccess && list.length === 0 && (
        <p className="rounded-xl bg-surface px-4 py-3 text-[15px] leading-[22px] text-ink-muted">
          Документов пока нет.
        </p>
      )}
      {list.length > 0 && (
        <DocumentList documents={list} selectedId={selected?.id} onSelect={select} now={now} />
      )}
      {selected && (
        <DocumentFacts
          key={selected.id}
          document={selected}
          proposals={proposals}
          me={user}
          now={now}
        />
      )}
    </section>
  );
}

/** Загрузка списка — скелетон строк (кадр 07в, «Состояния»). */
function Skeleton() {
  return (
    <div aria-busy="true" className="flex flex-col gap-2">
      <span className="sr-only">Загрузка документов…</span>
      {[0, 1].map((i) => (
        <div key={i} className="flex gap-3 rounded-xl bg-surface px-4 py-3">
          <div className="size-5 rounded-full bg-line" />
          <div className="flex flex-1 flex-col gap-2">
            <div className="h-4 w-1/2 rounded bg-line" />
            <div className="h-3 w-3/4 rounded bg-line" />
          </div>
        </div>
      ))}
    </div>
  );
}
