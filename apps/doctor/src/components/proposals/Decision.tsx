import { $api } from "@compass/api-client";
import { Alert, Button } from "@compass/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { decisionError, type Outcome, type QueueItem } from "../../card/proposalQueue";
import { refreshProposals } from "../../card/queries";
import { createSubmitGuard } from "../../card/submitGuard";

// Код-ревью #96: после решения выбор сразу переходит к следующему предложению, и второй
// щелчок двойного клика попал бы в его «Подтвердить». У пункта, выбранного автоматически,
// кнопки решения неактивны 500 мс после появления, и при «Уменьшить движение» тоже.
const GUARD_MS = 500;

/**
 * «Подтвердить предложение» и «Отклонить с причиной» (кадр 04): существующие
 * `POST /proposals/{id}/confirm` и `/reject`. Причина необязательна, «Отклонить» активна
 * сразу. После решения, 409 «уже решено» и 404 список уходит дальше (`onDecided`),
 * при 409 «нельзя применить» и 422 сообщение остаётся у предложения. Enter в списке ставит
 * фокус на кнопку с `data-primary-action`. `guard` — пункт выбран автоматически после
 * решения: кнопки включаются через `GUARD_MS`.
 */
export function Decision({
  item,
  guard,
  onDecided,
}: {
  item: QueueItem;
  guard: boolean;
  onDecided: (outcome: Outcome) => void;
}) {
  const queryClient = useQueryClient();
  const reasonId = useId();
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [armed, setArmed] = useState(!guard);
  // Второй щелчок двойного клика приходит до того, как `isPending` обновит кнопку (D1-15).
  const sending = useRef(createSubmitGuard());
  useEffect(() => {
    if (armed) {
      return;
    }
    const timer = setTimeout(() => setArmed(true), GUARD_MS);
    return () => clearTimeout(timer);
  }, [armed]);
  const settle = (kind: "confirmed" | "rejected") => ({
    onSuccess: () => {
      onDecided({ kind });
      void refreshProposals(queryClient);
    },
    onError: (error: unknown) => {
      const failure = decisionError(error);
      if (failure.gone) {
        onDecided({ kind: "gone", text: failure.text });
        void refreshProposals(queryClient);
      }
    },
    onSettled: () => sending.current.release(),
  });
  const confirm = $api.useMutation(
    "post",
    "/api/v1/proposals/{proposal_id}/confirm",
    settle("confirmed"),
  );
  const reject = $api.useMutation(
    "post",
    "/api/v1/proposals/{proposal_id}/reject",
    settle("rejected"),
  );
  const params = { path: { proposal_id: item.id } };
  const busy = confirm.isPending || reject.isPending || !armed;
  const failure = confirm.error ?? reject.error;
  const error = failure ? decisionError(failure) : null;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-2">
        <Button
          data-primary-action
          disabled={busy}
          onClick={() => {
            if (!sending.current.take()) return;
            reject.reset();
            confirm.mutate({ params, body: {} });
          }}
        >
          Подтвердить предложение
        </Button>
        {!rejecting && (
          <Button
            variant="secondary"
            disabled={busy}
            onClick={() => {
              setRejecting(true);
              requestAnimationFrame(() => reasonRef.current?.focus());
            }}
          >
            Отклонить с причиной
          </Button>
        )}
      </div>
      {rejecting && (
        <div className="flex flex-col gap-2 motion-safe:animate-fade-in motion-reduce:animate-fade-in">
          <label htmlFor={reasonId} className="text-sm font-medium text-ink">
            Причина отклонения, необязательно
          </label>
          <textarea
            ref={reasonRef}
            id={reasonId}
            className="min-h-16 rounded-md border border-line px-3 py-2 text-[15px]"
            maxLength={2000}
            placeholder="Например: приняла позже, уточнила в чате"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <div className="flex flex-wrap gap-2">
            <Button
              variant="secondary"
              disabled={busy}
              onClick={() => {
                if (!sending.current.take()) return;
                confirm.reset();
                reject.mutate({ params, body: { comment: reason.trim() || null } });
              }}
            >
              Отклонить
            </Button>
            <Button variant="quiet" disabled={busy} onClick={() => setRejecting(false)}>
              Отмена
            </Button>
          </div>
        </div>
      )}
      {error && !error.gone && <Alert tone="error">{error.text}.</Alert>}
    </div>
  );
}
