import { $api, type Schemas } from "@compass/api-client";
import { Alert, Button } from "@compass/ui";
import { TriangleAlert } from "lucide-react";
import { useId, useState } from "react";
import { type ChosenRegimen, doseError, isOffLabel } from "../../card/therapy";
import { Sheet } from "../details/Sheet";
import { therapyError } from "./errors";

const MIN_REASON = 10;

/**
 * «Изменить схему» (спека #28, 8.4): доза, кратность, время приёма, длительность. Схема
 * вне инструкции подсвечивается, обоснование от 10 символов обязательно. Доза — число
 * больше нуля, иначе ошибка под полем и кнопка неактивна (D3-10). Сохранение —
 * `POST /therapy/proposals/{id}/revise`: назначение по схеме врача.
 */
export function ReviseSheet({
  proposalId,
  label,
  onClose,
  onDecided,
}: {
  proposalId: string;
  label: Schemas["LabelRegimen"];
  onClose: () => void;
  /** Назначение сохранено: лист уже закрыт, экран обновляет данные. */
  onDecided: () => void;
}) {
  const warningId = useId();
  const [chosen, setChosen] = useState<ChosenRegimen>({
    dose_mg: label.dose_mg,
    times_per_day: label.times_per_day,
    duration_days: label.duration_days ?? null,
  });
  const [comment, setComment] = useState("");
  const revise = $api.useMutation("post", "/api/v1/therapy/proposals/{proposal_id}/revise");
  const offLabel = isOffLabel(label, chosen);
  const reasonMissing = offLabel && comment.trim().length < MIN_REASON;
  const doseProblem = doseError(chosen.dose_mg);

  return (
    <Sheet
      title="Изменить схему"
      description="Назначение по вашей схеме. Предложение системы останется в истории."
      onClose={onClose}
    >
      {(close) => (
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            revise.mutate(
              {
                params: { path: { proposal_id: proposalId } },
                body: { regimen: chosen, comment: comment.trim() || null },
              },
              {
                onSuccess: () => {
                  close();
                  onDecided();
                },
              },
            );
          }}
        >
          <div className="flex flex-wrap gap-3">
            <NumberField
              label="Разовая доза, мг"
              value={chosen.dose_mg}
              error={doseProblem}
              onChange={(v) => setChosen({ ...chosen, dose_mg: v ?? label.dose_mg })}
            />
            <NumberField
              label="Раз в сутки"
              value={chosen.times_per_day}
              onChange={(v) => setChosen({ ...chosen, times_per_day: v ?? label.times_per_day })}
            />
            <NumberField
              label="Длительность, дней"
              hint="Пусто — без срока"
              value={chosen.duration_days ?? null}
              onChange={(v) => setChosen({ ...chosen, duration_days: v })}
            />
          </div>
          {chosen.times_per_day !== label.times_per_day && (
            <label className="flex flex-col gap-1 text-sm">
              Время приёма
              <input
                className="min-h-10 rounded-lg border border-line px-3"
                maxLength={200}
                placeholder="например, вечером"
                value={chosen.schedule ?? ""}
                onChange={(e) => setChosen({ ...chosen, schedule: e.target.value || null })}
              />
              <span className="text-xs text-ink-muted">Пусто — время приёма определяет врач</span>
            </label>
          )}
          {offLabel && (
            <p
              id={warningId}
              role="status"
              className="flex items-center gap-2 rounded-lg bg-advised-soft px-3 py-2 text-sm font-semibold text-advised"
            >
              <TriangleAlert aria-hidden className="size-4" />
              Вне инструкции: нужно обоснование, не короче {MIN_REASON} символов
            </p>
          )}
          <label className="flex flex-col gap-1 text-sm">
            {offLabel ? "Обоснование, обязательно" : "Комментарий"}
            <textarea
              className="min-h-20 rounded-lg border border-line px-3 py-2"
              maxLength={2000}
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              aria-invalid={reasonMissing}
              aria-describedby={offLabel ? warningId : undefined}
            />
          </label>
          {revise.isError && <Alert tone="error">{therapyError(revise.error)}</Alert>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={close}>
              Отмена
            </Button>
            <Button
              type="submit"
              disabled={revise.isPending || reasonMissing || doseProblem !== null}
            >
              Назначить по этой схеме
            </Button>
          </div>
        </form>
      )}
    </Sheet>
  );
}

function NumberField({
  label,
  hint,
  error = null,
  value,
  onChange,
}: {
  label: string;
  hint?: string;
  /** Ошибка под полем; поле помечено недействительным. */
  error?: string | null;
  value: number | null;
  onChange: (value: number | null) => void;
}) {
  const errorId = useId();
  return (
    <label className="flex flex-col gap-1 text-sm">
      {label}
      <input
        className={`min-h-10 w-40 rounded-lg border px-3 ${error ? "border-danger" : "border-line"}`}
        type="number"
        min={1}
        value={value ?? ""}
        aria-invalid={error !== null}
        aria-describedby={error ? errorId : undefined}
        onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
      />
      {error && (
        <span id={errorId} role="alert" className="text-xs font-medium text-danger">
          {error}
        </span>
      )}
      {hint && <span className="text-xs text-ink-muted">{hint}</span>}
    </label>
  );
}
