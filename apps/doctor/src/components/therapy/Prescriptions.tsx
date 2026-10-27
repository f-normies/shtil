import { $api, type Schemas } from "@compass/api-client";
import { Alert, Button } from "@compass/ui";
import { useQueryClient } from "@tanstack/react-query";
import { TriangleAlert } from "lucide-react";
import { useState } from "react";
import { formatDate } from "../../card/format";
import { refreshTherapy, usePrescriptions, useReactionReports } from "../../card/queries";
import { reportsOf, stopFormOpen, timesText } from "../../card/therapy";
import { Reactions } from "./Reactions";

type Prescription = Schemas["PrescriptionOut"];
type ReactionReport = Schemas["ReactionReportOut"];

/** `ends_on` — первый день без приёма; врачу показываем последний день приёма. */
function lastDay(endsOn: string): string {
  const day = new Date(`${endsOn}T00:00:00`);
  day.setDate(day.getDate() - 1);
  return day.toISOString();
}

const STATUS: Record<Prescription["status"], string> = {
  active: "действует",
  completed: "курс завершён",
  stopped: "отменено",
};

/**
 * Назначения пациента: действующие и прошлые, отмена с причиной (спека #28, раздел 8),
 * отметки нежелательных реакций и черновик извещения (спека #29, раздел 7).
 */
export function Prescriptions({
  patientId,
  compact = false,
  onChanged,
}: {
  patientId: string;
  compact?: boolean;
  /** Назначение отменено: ответ подбора на экране устарел. */
  onChanged?: () => void;
}) {
  const prescriptions = usePrescriptions(patientId);
  const reactions = useReactionReports(patientId);
  const shown = (prescriptions.data ?? []).filter((p) => !compact || p.status === "active");

  return (
    <div className="flex flex-col gap-3">
      {prescriptions.isError && (
        <p className="text-sm text-danger">Не удалось загрузить назначения.</p>
      )}
      {prescriptions.data && shown.length === 0 && (
        <p className="text-sm text-ink-muted">
          {compact ? "Действующих назначений нет." : "Назначений нет."}
        </p>
      )}
      {reactions.isError && (
        <p className="text-sm text-danger">Не удалось загрузить отметки нежелательных реакций.</p>
      )}
      {shown.map((p) => (
        <PrescriptionItem
          key={p.id}
          prescription={p}
          reports={reportsOf(reactions.data, p.id)}
          canStop={!compact}
          onChanged={onChanged}
        />
      ))}
    </div>
  );
}

function PrescriptionItem({
  prescription: p,
  reports,
  canStop,
  onChanged,
}: {
  prescription: Prescription;
  reports: ReactionReport[];
  canStop: boolean;
  onChanged?: () => void;
}) {
  const queryClient = useQueryClient();
  const [stopping, setStopping] = useState(false);
  const [reason, setReason] = useState("");
  const stop = $api.useMutation("post", "/api/v1/prescriptions/{prescription_id}/stop", {
    onSuccess: async () => {
      setStopping(false);
      await refreshTherapy(queryClient);
      onChanged?.();
    },
  });

  return (
    <article className="flex flex-col gap-2 rounded-md border border-line bg-surface p-3 text-sm">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-semibold text-ink">
          {p.trade_name} — по {p.dose_mg} мг {timesText(p.times_per_day)} в сутки, {p.schedule}
        </h3>
        <span className={p.status === "active" ? "font-medium text-ink" : "text-ink-muted"}>
          {STATUS[p.status]}
        </span>
      </header>
      <p className="text-ink-muted">
        С {formatDate(p.started_at)}
        {p.ends_on && ` по ${formatDate(lastDay(p.ends_on))} включительно`}
        {!p.ends_on && p.status === "active" && ", длительность определяет врач"}
        {p.stopped_at && ` · отменено ${formatDate(p.stopped_at)}: ${p.stop_reason ?? ""}`}
      </p>
      {p.off_label && (
        // Решение врача с обоснованием, а не сигнал: нейтральный тон, значок и слова (#36).
        <p className="flex items-center gap-2 rounded bg-surface-sunken px-2 py-1 font-medium text-ink">
          <TriangleAlert aria-hidden className="size-4" />
          Вне инструкции. Обоснование врача: {p.off_label_reason}
        </p>
      )}
      <Reactions prescriptionId={p.id} reports={reports} canDraft={canStop} />
      {canStop && p.status === "active" && !stopping && (
        <div>
          <Button variant="secondary" onClick={() => setStopping(true)}>
            Отменить назначение…
          </Button>
        </div>
      )}
      {stopFormOpen(p.status, stopping) && (
        <form
          className="flex flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            stop.mutate({
              params: { path: { prescription_id: p.id } },
              body: { reason: reason.trim() },
            });
          }}
        >
          <label className="flex flex-col gap-1">
            Причина отмены (обязательно)
            <textarea
              className="min-h-16 rounded-md border border-line px-2 py-1"
              minLength={3}
              maxLength={500}
              required
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          {stop.isError && <Alert tone="error">отмена не сохранена. Попробуйте ещё раз.</Alert>}
          <div className="flex gap-2">
            <Button type="submit" disabled={stop.isPending || reason.trim().length < 3}>
              Отменить назначение
            </Button>
            <Button type="button" variant="secondary" onClick={() => setStopping(false)}>
              Не отменять
            </Button>
          </div>
        </form>
      )}
    </article>
  );
}
