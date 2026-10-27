import { $api, type Schemas } from "@compass/api-client";
import { Alert, Button, Panel, SegmentedControl } from "@compass/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { formatDateTime } from "../card/format";
import { shortName } from "../card/names";
import { accessText, stepsText } from "./escalations";

type Escalation = Schemas["EscalationOut"];

export const ESCALATIONS = "/api/v1/admin/escalations";
const STATUS = [
  { value: "open", label: "Не разобраны" },
  { value: "all", label: "Все" },
] as const;

/**
 * «Эскалации» (спека #56, раздел 6): кому и когда передан сигнал, кто взял его в работу,
 * доступ заместителя к карте. Без ФИО пациента, заголовка и уровня — их нет и в API.
 */
export function EscalationsSection() {
  const [status, setStatus] = useState<"open" | "all">("open");
  const queryClient = useQueryClient();
  const escalations = $api.useQuery("get", ESCALATIONS, { params: { query: { status } } });
  const review = $api.useMutation("post", "/api/v1/admin/escalations/{alert_id}/review", {
    onSettled: () =>
      queryClient.invalidateQueries({ predicate: (q) => String(q.queryKey[1]) === ESCALATIONS }),
  });
  const now = new Date();

  return (
    <section aria-labelledby="escalations" className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-4">
        <h2 id="escalations" className="text-lg font-semibold">
          Эскалации
        </h2>
        <SegmentedControl
          ariaLabel="Какие эскалации показать"
          options={STATUS}
          value={status}
          onValueChange={(v) => setStatus(v as "open" | "all")}
        />
      </div>
      <Panel>
        {escalations.isPending && <p className="text-sm text-ink-muted">Загрузка…</p>}
        {escalations.isError && <Alert tone="error">Не удалось загрузить эскалации.</Alert>}
        {escalations.data && escalations.data.length === 0 && (
          <p className="text-sm text-ink-muted">
            {status === "open" ? "Неразобранных эскалаций нет." : "Эскалаций не было."}
          </p>
        )}
        {escalations.data && escalations.data.length > 0 && (
          <table className="w-full text-left align-top text-sm">
            <caption className="sr-only">Эскалации сигналов «Обязательно»</caption>
            <thead className="text-xs text-ink-muted uppercase">
              <tr>
                <th className="py-2 font-semibold">Сигнал</th>
                <th className="py-2 font-semibold">Ведущий врач</th>
                <th className="py-2 font-semibold">Шаги</th>
                <th className="py-2 font-semibold">Взят в работу</th>
                <th className="py-2 font-semibold">Доступ заместителя</th>
                <th className="py-2 font-semibold">Разбор</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {escalations.data.map((e) => (
                <Row
                  key={e.alert_id}
                  escalation={e}
                  now={now}
                  busy={review.isPending}
                  onReview={() => review.mutate({ params: { path: { alert_id: e.alert_id } } })}
                />
              ))}
            </tbody>
          </table>
        )}
        {review.isError && <Alert tone="error">Не удалось отметить разбор.</Alert>}
      </Panel>
    </section>
  );
}

function Row({
  escalation: e,
  now,
  busy,
  onReview,
}: {
  escalation: Escalation;
  now: Date;
  busy: boolean;
  onReview: () => void;
}) {
  const name = (id: string | null) => (id && e.names[id] ? shortName(e.names[id]) : "");
  return (
    <tr>
      <td className="py-2 font-mono">{e.number}</td>
      <td className="py-2">{name(e.doctor_id)}</td>
      <td className="py-2">
        <ul className="flex flex-col gap-0.5">
          {stepsText(e).map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </td>
      <td className="py-2">
        {e.acknowledged_at
          ? `${formatDateTime(e.acknowledged_at)}, ${name(e.acknowledged_by)}`
          : "Ещё нет"}
      </td>
      <td className="py-2">{accessText(e, now)}</td>
      <td className="py-2">
        {e.reviewed_at ? (
          `Разобрано ${formatDateTime(e.reviewed_at)}`
        ) : (
          <Button variant="secondary" disabled={busy} onClick={onReview}>
            Разобрано
          </Button>
        )}
      </td>
    </tr>
  );
}
