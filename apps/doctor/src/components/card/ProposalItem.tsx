import { $api, type Schemas } from "@compass/api-client";
import { Alert, Button, StatusBadge } from "@compass/ui";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { TriangleAlert } from "lucide-react";
import { useState } from "react";
import { detailOf } from "../../card/errors";
import { formatDateTime } from "../../card/format";
import { caseHistoryDocument, isTherapy, proposalView } from "../../card/proposals";
import { refreshProposals } from "../../card/queries";
import { isReferral } from "../../card/referral";

type Proposal = Schemas["ProposalOut"];

const ALREADY_DECIDED = "Proposal is already decided";
const CANNOT_APPLY = "Proposal kind cannot be applied";

/** Ошибка решения по-русски: тексты API врачу не показываем (спека #13, 4.1). */
function errorText(error: unknown): string {
  const detail = detailOf(error);
  if (detail === ALREADY_DECIDED) {
    return "предложение уже рассмотрено. Обновите список, чтобы увидеть решение.";
  }
  if (detail === CANNOT_APPLY) {
    return "система не умеет применять предложения этого вида. Решение не сохранено.";
  }
  return "решение не сохранено. Проверьте связь и попробуйте ещё раз.";
}

/** Предложение системы: подтвердить или отклонить с необязательной причиной. */
export function ProposalItem({
  proposal,
  currentDoctorId,
}: {
  proposal: Proposal;
  currentDoctorId: string;
}) {
  const view = proposalView(proposal);
  const documentId = caseHistoryDocument(proposal);
  const queryClient = useQueryClient();
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  // После 409 «уже решено» список не обновляем сразу: иначе предложение исчезнет вместе
  // с объяснением. Врач обновляет его сам кнопкой у сообщения.
  const options = { onSuccess: () => refreshProposals(queryClient) };
  const confirm = $api.useMutation("post", "/api/v1/proposals/{proposal_id}/confirm", options);
  const reject = $api.useMutation("post", "/api/v1/proposals/{proposal_id}/reject", options);
  const params = { path: { proposal_id: proposal.id } };
  const busy = confirm.isPending || reject.isPending;
  const error = confirm.error ?? reject.error;
  const proposed = proposal.status === "proposed";
  const decide = (choice: "confirm" | "reject") => {
    confirm.reset();
    reject.reset();
    if (choice === "confirm") {
      confirm.mutate({ params, body: {} });
    } else {
      reject.mutate({ params, body: { comment: reason.trim() || null } });
    }
  };

  return (
    <article
      className={`flex flex-col gap-2 rounded-md border border-line bg-surface p-3 ${proposed ? "border-dashed border-primary-text" : "border-solid"}`}
    >
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-semibold text-ink">{view.title}</h3>
        <div className="flex flex-wrap items-center gap-2">
          {proposed && <StatusBadge status="proposed" />}
          {proposal.status === "confirmed" && <StatusBadge status="confirmed" />}
          <span className="text-xs text-ink-muted">
            {[view.recordLabel, formatDateTime(proposal.created_at)].filter(Boolean).join(" · ")}
          </span>
        </div>
      </header>
      {view.summary && <p className="text-sm text-ink">{view.summary}</p>}
      {view.quote && (
        <blockquote className="border-l-2 border-ink-muted pl-3 text-sm italic">
          «{view.quote}»
        </blockquote>
      )}
      {view.adherence && <p className="text-sm">Приём: {view.adherence}</p>}
      {view.drug && (
        <p className="text-sm">
          Препарат: {view.drug.name}
          {view.drug.warning && (
            <span className="ml-2 inline-flex items-center gap-1 rounded bg-warning-soft px-1.5 py-0.5 font-medium text-warning">
              <TriangleAlert aria-hidden className="size-3.5" />
              {view.drug.warning}
            </span>
          )}
        </p>
      )}
      {view.fields.length > 0 && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 text-sm">
          {view.fields.map((f) => (
            <div key={f.name} className="contents">
              <dt className="text-ink-muted">{f.name}</dt>
              <dd className="break-words">{f.value}</dd>
            </div>
          ))}
        </dl>
      )}

      {error && (
        <div className="flex flex-col gap-2">
          <Alert tone="error">{errorText(error)}</Alert>
          {detailOf(error) === ALREADY_DECIDED && (
            <div>
              <Button variant="secondary" onClick={() => refreshProposals(queryClient)}>
                Обновить список
              </Button>
            </div>
          )}
        </div>
      )}

      {proposal.status === "proposed" && isReferral(proposal) ? (
        // Направление решается на экране сигнала вместе с сигналами (спека #35).
        <Link
          className="text-sm font-medium text-primary underline"
          to="/alerts/$alertId"
          params={{ alertId: String(proposal.payload.alert_id ?? "") }}
          search={{ patient: proposal.patient_id }}
        >
          Открыть сигнал →
        </Link>
      ) : proposal.status === "proposed" && isTherapy(proposal) ? (
        // Назначение решается там, где видны проверки и источники (спека #28, раздел 8).
        <Link
          className="text-sm font-medium text-primary underline"
          to="/patients/$patientId/therapy"
          params={{ patientId: proposal.patient_id }}
        >
          Рассмотреть в разделе «Терапия» →
        </Link>
      ) : proposal.status === "proposed" && documentId ? (
        // Факт истории болезни решается рядом с документом, цитатой и сверкой (спека #30, 6).
        <Link
          className="text-sm font-medium text-primary underline"
          to="/patients/$patientId/documents"
          params={{ patientId: proposal.patient_id }}
          search={{ document: documentId }}
        >
          Рассмотреть в истории болезни →
        </Link>
      ) : proposal.status === "proposed" ? (
        <div className="flex flex-col gap-2">
          {rejecting && (
            <label className="flex flex-col gap-1 text-sm">
              Причина (необязательно)
              <textarea
                className="min-h-16 rounded-md border border-line px-2 py-1"
                maxLength={2000}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
            </label>
          )}
          <div className="flex flex-wrap gap-2">
            {rejecting ? (
              <>
                <Button disabled={busy} onClick={() => decide("reject")}>
                  Отклонить
                </Button>
                <Button variant="secondary" disabled={busy} onClick={() => setRejecting(false)}>
                  Отмена
                </Button>
              </>
            ) : (
              <>
                <Button disabled={busy} onClick={() => decide("confirm")}>
                  Подтвердить
                </Button>
                <Button variant="secondary" disabled={busy} onClick={() => setRejecting(true)}>
                  Отклонить…
                </Button>
              </>
            )}
          </div>
        </div>
      ) : (
        <p className="text-sm text-ink-muted">
          {proposal.status === "confirmed" ? "Подтверждено" : "Отклонено"}{" "}
          {proposal.decided_by === currentDoctorId ? "вами" : "другим врачом"}
          {proposal.decided_at && `, ${formatDateTime(proposal.decided_at)}`}
          {proposal.decision_comment && `. Причина: ${proposal.decision_comment}`}
        </p>
      )}
    </article>
  );
}
