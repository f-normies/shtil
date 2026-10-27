import { $api, type Schemas } from "@compass/api-client";
import { Alert, Button } from "@compass/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";
import { canSaveDecision } from "../../card/alerts";
import { refreshAlerts } from "../../card/queries";

type AlertOut = Schemas["AlertOut"];
type DecisionOption = Schemas["DecisionOptionOut"];

/**
 * «Зафиксировать решение» (кадр 03): радиогруппа вариантов из данных и причина.
 * У «Обязательно» причина обязательна: пока поле пустое, «Сохранить решение» неактивна.
 */
export function DecisionForm({ alert, options }: { alert: AlertOut; options: DecisionOption[] }) {
  const id = useId();
  const queryClient = useQueryClient();
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const [decision, setDecision] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const resolve = $api.useMutation("post", "/api/v1/alerts/{alert_id}/resolve", {
    onSettled: () => refreshAlerts(queryClient),
  });
  const required = alert.level === "mandatory";
  const hint = options.find((o) => o.id === decision)?.reason_hint;
  const ready = canSaveDecision(alert.level, decision, reason);

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (!ready) {
          reasonRef.current?.focus();
          return;
        }
        resolve.mutate({
          params: { path: { alert_id: alert.id } },
          body: { decision_id: decision ?? "", reason: reason.trim() || null },
        });
      }}
    >
      <fieldset className="flex flex-col divide-y divide-line">
        <legend className="sr-only">Вариант решения</legend>
        {options.map((o) => (
          <label key={o.id} className="flex cursor-pointer items-center gap-3 py-3">
            <input
              type="radio"
              name={`${id}-decision`}
              className="size-5 accent-primary"
              checked={decision === o.id}
              onChange={() => setDecision(o.id)}
            />
            <span className={decision === o.id ? "font-semibold" : ""}>{o.text}</span>
          </label>
        ))}
      </fieldset>
      <div className="flex flex-col gap-1.5">
        <label htmlFor={`${id}-reason`} className="text-sm font-medium">
          {required ? "Причина решения, обязательно" : "Комментарий"}
        </label>
        <textarea
          ref={reasonRef}
          id={`${id}-reason`}
          className="min-h-11 rounded-md border border-line px-3 py-2"
          maxLength={2000}
          placeholder={hint ? `Опишите: ${hint}` : "Опишите, что сделано и почему"}
          value={reason}
          aria-invalid={resolve.isError || undefined}
          onChange={(e) => setReason(e.target.value)}
        />
      </div>
      {resolve.isError && (
        <Alert tone="error">
          {resolve.error && "detail" in resolve.error && typeof resolve.error.detail === "string"
            ? resolve.error.detail
            : "Не удалось сохранить решение. Попробуйте ещё раз."}
        </Alert>
      )}
      <div>
        <Button type="submit" disabled={resolve.isPending || decision === null}>
          Сохранить решение
        </Button>
      </div>
    </form>
  );
}
