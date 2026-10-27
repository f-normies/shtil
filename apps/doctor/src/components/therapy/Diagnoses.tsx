import { $api } from "@compass/api-client";
import { Alert, Button } from "@compass/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { formatDate } from "../../card/format";
import { refreshTherapy, useDiagnoses } from "../../card/queries";

/** Коды показаний справочника: подсказки при вводе диагноза. Полного МКБ-10 в системе нет. */
const SUGGESTIONS = [
  ["F41.1", "Генерализованное тревожное расстройство"],
  ["F43.2", "Расстройство адаптации"],
  ["F43.8", "Другие реакции на тяжёлый стресс"],
  ["U09.9", "Состояние после COVID-19"],
  ["F32.1", "Депрессивный эпизод средней степени"],
] as const;

/** Диагнозы ставит врач; снятый диагноз остаётся в списке зачёркнутым (спека #28). */
export function Diagnoses({ patientId, onChanged }: { patientId: string; onChanged: () => void }) {
  const queryClient = useQueryClient();
  const diagnoses = useDiagnoses(patientId);
  const [code, setCode] = useState("");
  const [title, setTitle] = useState("");
  // Подбор по старым диагнозам устарел: сервер отклонил его предложения.
  const onSuccess = async () => {
    onChanged();
    await refreshTherapy(queryClient);
  };
  const add = $api.useMutation("post", "/api/v1/patients/{patient_id}/diagnoses", {
    onSuccess: async () => {
      setCode("");
      setTitle("");
      await onSuccess();
    },
  });
  const resolve = $api.useMutation(
    "post",
    "/api/v1/patients/{patient_id}/diagnoses/{diagnosis_id}/resolve",
    { onSuccess },
  );

  return (
    <div className="flex flex-col gap-3">
      {diagnoses.isError && <p className="text-sm text-danger">Не удалось загрузить диагнозы.</p>}
      {diagnoses.data && diagnoses.data.length === 0 && (
        <p className="text-sm text-ink-muted">Диагнозов нет.</p>
      )}
      <ul className="flex flex-col gap-2">
        {diagnoses.data?.map((d) => (
          <li key={d.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
            <span className={d.resolved_at ? "text-ink-muted line-through" : "text-ink"}>
              <span className="font-mono font-semibold">{d.icd10}</span> {d.title}
              <span className="ml-2 text-xs text-ink-muted no-underline">
                {d.resolved_at
                  ? `снят ${formatDate(d.resolved_at)}`
                  : `поставлен ${formatDate(d.set_at)}`}
              </span>
            </span>
            {!d.resolved_at && (
              <Button
                variant="secondary"
                disabled={resolve.isPending}
                onClick={() =>
                  resolve.mutate({
                    params: { path: { patient_id: patientId, diagnosis_id: d.id } },
                  })
                }
              >
                Снять
              </Button>
            )}
          </li>
        ))}
      </ul>
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          add.mutate({
            params: { path: { patient_id: patientId } },
            body: { icd10: code, title: title.trim() },
          });
        }}
      >
        <label className="flex flex-col gap-1 text-sm">
          Код МКБ-10
          <input
            className="min-h-11 w-28 rounded-md border border-line px-3 font-mono"
            list="icd10-suggestions"
            placeholder="F41.1"
            value={code}
            onChange={(e) => {
              const value = e.target.value.toUpperCase();
              setCode(value);
              const known = SUGGESTIONS.find(([c]) => c === value);
              if (known && !title) {
                setTitle(known[1]);
              }
            }}
            required
          />
          <datalist id="icd10-suggestions">
            {SUGGESTIONS.map(([c, t]) => (
              <option key={c} value={c}>
                {t}
              </option>
            ))}
          </datalist>
        </label>
        <label className="flex min-w-64 flex-1 flex-col gap-1 text-sm">
          Название
          <input
            className="min-h-11 rounded-md border border-line px-3"
            maxLength={200}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
          />
        </label>
        <Button type="submit" disabled={add.isPending}>
          Поставить диагноз
        </Button>
      </form>
      {add.isError && (
        <Alert tone="error">код МКБ-10 в формате F41.1 и название обязательны.</Alert>
      )}
    </div>
  );
}
