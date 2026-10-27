import { $api } from "@compass/api-client";
import { Alert, Button } from "@compass/ui";
import { useQueryClient } from "@tanstack/react-query";
import { CircleHelp } from "lucide-react";
import { useState } from "react";
import { formatDate } from "../../card/format";
import { refreshTherapy, useMedications } from "../../card/queries";
import { innChoices, stopBeforeStart, todayIso } from "../../card/therapy";

const CHECKS: Record<string, string> = {
  registered: "зарегистрирован",
  inactive: "регистрация не действует",
  suspended: "применение приостановлено",
  unknown: "статус не определён",
  similar: "похожие названия",
  not_found: "нет в ГРЛС",
};

/**
 * Препараты, которые пациент принимает вне системы (спека #29). По их МНН проверяются
 * взаимодействия и срок после отмены ингибитора МАО. Препарат без МНН подбор отметит
 * как «проверьте взаимодействия сами».
 */
export function Medications({
  patientId,
  onChanged,
  readOnly = false,
}: {
  patientId: string;
  onChanged?: () => void;
  /** Минимальный набор заместителя: только сведения, без изменения терапии. */
  readOnly?: boolean;
}) {
  const queryClient = useQueryClient();
  const medications = useMedications(patientId);
  const [name, setName] = useState("");
  const [checked, setChecked] = useState("");
  const [inn, setInn] = useState("");
  const [startedOn, setStartedOn] = useState("");
  const [stoppedOn, setStoppedOn] = useState("");
  const lookup = $api.useQuery(
    "get",
    "/api/v1/therapy/drug-lookup",
    { params: { query: { name: checked } } },
    { enabled: checked.length > 0 },
  );
  const onSuccess = async () => {
    onChanged?.();
    await refreshTherapy(queryClient);
  };
  const add = $api.useMutation("post", "/api/v1/patients/{patient_id}/medications", {
    onSuccess: async () => {
      setName("");
      setChecked("");
      setInn("");
      setStartedOn("");
      setStoppedOn("");
      await onSuccess();
    },
  });
  const stop = $api.useMutation(
    "post",
    "/api/v1/patients/{patient_id}/medications/{medication_id}/stop",
    { onSuccess },
  );
  const choices = lookup.data ? innChoices(lookup.data) : [];
  const datesWrong = stopBeforeStart(startedOn, stoppedOn);
  const today = todayIso();

  return (
    <div className="flex flex-col gap-3">
      {medications.isError && (
        <p className="text-sm text-danger">Не удалось загрузить препараты.</p>
      )}
      {medications.data && medications.data.length === 0 && (
        <p className="text-sm text-ink-muted">Других препаратов нет.</p>
      )}
      <ul className="flex flex-col gap-2">
        {medications.data?.map((m) => (
          <li key={m.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
            <span className={m.stopped_on ? "text-ink-muted" : "text-ink"}>
              <span className="font-medium">{m.name}</span>
              {m.inn ? (
                <span className="ml-1 text-ink-muted">({m.inn.toLowerCase()})</span>
              ) : (
                // Пометка о данных, а не уровень сигнала: без цвета «Желательно» (#36).
                <span className="ml-2 inline-flex items-center gap-1 text-ink-muted">
                  <CircleHelp aria-hidden className="size-3.5" />
                  МНН не определён
                </span>
              )}
              <span className="ml-2 text-xs text-ink-muted">
                {[
                  CHECKS[m.registry_check] ?? m.registry_check,
                  m.started_on && `с ${formatDate(m.started_on)}`,
                  m.stopped_on ? `отменён ${formatDate(m.stopped_on)}` : "принимает",
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
            </span>
            {!readOnly && !m.stopped_on && (
              <Button
                variant="secondary"
                disabled={stop.isPending || stopBeforeStart(m.started_on, today)}
                onClick={() =>
                  stop.mutate({
                    params: { path: { patient_id: patientId, medication_id: m.id } },
                    body: { stopped_on: today },
                  })
                }
              >
                Отменён сегодня
              </Button>
            )}
          </li>
        ))}
      </ul>
      {stop.isError && <Alert tone="error">не удалось отметить отмену препарата.</Alert>}
      {!readOnly && (
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            add.mutate({
              params: { path: { patient_id: patientId } },
              body: {
                name: name.trim(),
                inn: inn || null,
                started_on: startedOn || null,
                stopped_on: stoppedOn || null,
              },
            });
          }}
        >
          <label className="flex min-w-48 flex-1 flex-col gap-1 text-sm">
            Препарат
            <input
              className="min-h-11 rounded-md border border-line px-3"
              maxLength={200}
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setInn("");
              }}
              onBlur={() => setChecked(name.trim())}
              required
            />
          </label>
          {choices.length > 0 && (
            <label className="flex flex-col gap-1 text-sm">
              {lookup.data?.check === "similar"
                ? "МНН (похожие названия в ГРЛС)"
                : "МНН (в ГРЛС их несколько)"}
              <select
                className="min-h-11 rounded-md border border-line px-3"
                value={inn}
                onChange={(e) => setInn(e.target.value)}
              >
                <option value="">не выбрано</option>
                {choices.map((c) => (
                  <option key={c.inn} value={c.inn}>
                    {c.label}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="flex flex-col gap-1 text-sm">
            Начат
            <input
              className="min-h-11 rounded-md border border-line px-3"
              type="date"
              value={startedOn}
              onChange={(e) => setStartedOn(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Отменён (пусто — принимает)
            <input
              className="min-h-11 rounded-md border border-line px-3"
              type="date"
              min={startedOn || undefined}
              value={stoppedOn}
              onChange={(e) => setStoppedOn(e.target.value)}
              aria-invalid={datesWrong}
            />
          </label>
          <Button type="submit" disabled={add.isPending || datesWrong}>
            Добавить препарат
          </Button>
        </form>
      )}
      {lookup.data && checked && (
        <p className="text-sm text-ink-muted">
          ГРЛС: {CHECKS[lookup.data.check] ?? lookup.data.check}
        </p>
      )}
      {datesWrong && <p className="text-sm text-danger">Дата отмены раньше даты начала приёма.</p>}
      {add.isError && <Alert tone="error">не удалось добавить препарат.</Alert>}
    </div>
  );
}
