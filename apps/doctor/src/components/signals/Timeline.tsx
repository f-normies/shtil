import type { Schemas } from "@compass/api-client";
import { Check, Clock } from "lucide-react";
import { timeline } from "../../card/alerts";
import { formatDateTime } from "../../card/format";

/** «Ход обработки» (кадр 03): упорядоченный список шагов, статус словами и значком. */
export function Timeline({
  alert,
  names,
}: {
  alert: Schemas["AlertOut"];
  names: Record<string, string>;
}) {
  const steps = timeline(alert, names);
  return (
    <ol className="flex flex-col divide-y divide-line">
      {steps.map((s) => (
        <li key={`${s.title}-${s.at ?? "pending"}`} className="flex gap-3 py-3">
          {s.done ? (
            <Check aria-label="выполнено" className="mt-0.5 size-5 shrink-0 text-ink-muted" />
          ) : (
            <Clock aria-label="ещё нет" className="mt-0.5 size-5 shrink-0 text-ink-muted" />
          )}
          <div className="flex-1">
            <p>{s.title}</p>
            {s.detail && <p className="text-sm text-ink-muted">{s.detail}</p>}
          </div>
          {s.at && <span className="text-sm text-ink-muted">{formatDateTime(s.at)}</span>}
        </li>
      ))}
    </ol>
  );
}
