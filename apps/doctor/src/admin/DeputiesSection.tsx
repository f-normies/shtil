import { $api } from "@compass/api-client";
import { Alert, Button, Panel, StatusIcon } from "@compass/ui";
import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { detailOf } from "../card/errors";
import { shortName } from "../card/names";
import { noDeputyText, periodText } from "./escalations";

const DOCTORS = "/api/v1/admin/doctors";
const DEPUTIES = "/api/v1/admin/deputies";
const FIELD = "min-h-11 rounded-md border border-line bg-surface px-3";

/**
 * «Заместители» (спека #56, раздел 6): врачи без заместителя на сегодня, действующие и
 * будущие назначения, форма назначения и отзыв. Пересечение периодов сервер отклоняет (409).
 * Отзыв закрывает доступы заместителя, поэтому сначала вопрос в строке.
 */
export function DeputiesSection() {
  const queryClient = useQueryClient();
  const doctors = $api.useQuery("get", DOCTORS);
  const deputies = $api.useQuery("get", DEPUTIES);
  const refresh = () =>
    queryClient.invalidateQueries({
      predicate: (q) => [DOCTORS, DEPUTIES].includes(String(q.queryKey[1])),
    });
  const [confirming, setConfirming] = useState<string | null>(null);
  const revoke = $api.useMutation("post", "/api/v1/admin/deputies/{assignment_id}/revoke", {
    onSettled: () => {
      setConfirming(null);
      return refresh();
    },
  });
  const missing = (doctors.data ?? []).filter((d) => d.no_deputy);
  const warning = noDeputyText(missing.length);

  return (
    <section aria-labelledby="deputies" className="flex flex-col gap-4">
      <h2 id="deputies" className="text-lg font-semibold">
        Заместители
      </h2>
      {warning && (
        <div role="status" className="flex items-start gap-3 rounded-lg bg-required-soft px-4 py-3">
          <StatusIcon status="required" decorative />
          <div className="flex flex-col gap-1 text-sm">
            <p className="font-semibold text-ink">{warning}</p>
            <p className="text-ink">{missing.map((d) => shortName(d.full_name)).join(", ")}</p>
          </div>
        </div>
      )}
      <AssignForm doctors={doctors.data ?? []} onDone={refresh} />
      <Panel>
        {deputies.isPending && <p className="text-sm text-ink-muted">Загрузка…</p>}
        {deputies.isError && <Alert tone="error">Не удалось загрузить назначения.</Alert>}
        {deputies.data && deputies.data.length === 0 && (
          <p className="text-sm text-ink-muted">Назначений нет.</p>
        )}
        {deputies.data && deputies.data.length > 0 && (
          <table className="w-full text-left text-sm">
            <caption className="sr-only">Действующие и будущие назначения</caption>
            <thead className="text-xs text-ink-muted uppercase">
              <tr>
                <th className="py-2 font-semibold">Врач</th>
                <th className="py-2 font-semibold">Заместитель</th>
                <th className="py-2 font-semibold">Период</th>
                <th className="py-2 font-semibold">Основание</th>
                <th className="py-2">
                  <span className="sr-only">Действие</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {deputies.data.map((d) => (
                <tr key={d.id}>
                  <td className="py-2">{shortName(d.doctor_name)}</td>
                  <td className="py-2">{shortName(d.deputy_name)}</td>
                  <td className="py-2 tabular-nums">{periodText(d.starts_on, d.ends_on)}</td>
                  <td className="py-2">{d.basis}</td>
                  <td className="py-2 text-right">
                    {confirming === d.id ? (
                      <div className="flex items-center justify-end gap-2">
                        <span className="text-ink">
                          Отозвать? Доступы заместителя к пациентам закроются.
                        </span>
                        <Button
                          variant="secondary"
                          disabled={revoke.isPending}
                          onClick={() =>
                            revoke.mutate({ params: { path: { assignment_id: d.id } } })
                          }
                        >
                          Да, отозвать
                        </Button>
                        <Button
                          variant="secondary"
                          disabled={revoke.isPending}
                          onClick={() => setConfirming(null)}
                        >
                          Отмена
                        </Button>
                      </div>
                    ) : (
                      <Button
                        variant="secondary"
                        disabled={revoke.isPending}
                        onClick={() => setConfirming(d.id)}
                      >
                        Отозвать
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {revoke.isError && <Alert tone="error">Не удалось отозвать назначение.</Alert>}
      </Panel>
    </section>
  );
}

/** Текст сервера у 409 и проверки назначения; у ошибки формы 422 — общий. */
function assignError(error: unknown): string {
  const detail = detailOf(error);
  return typeof detail === "string" ? detail : "Не удалось назначить заместителя. Проверьте поля.";
}

function AssignForm({
  doctors,
  onDone,
}: {
  doctors: readonly { id: string; full_name: string }[];
  onDone: () => Promise<void>;
}) {
  const [doctorId, setDoctorId] = useState("");
  const [deputyId, setDeputyId] = useState("");
  const [startsOn, setStartsOn] = useState("");
  const [endsOn, setEndsOn] = useState("");
  const [basis, setBasis] = useState("");
  const assign = $api.useMutation("post", DEPUTIES, {
    onSuccess: async () => {
      setDeputyId("");
      setBasis("");
      await onDone();
    },
  });
  const ready = doctorId && deputyId && startsOn && endsOn && basis.trim();
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (ready) {
      assign.mutate({
        body: {
          doctor_id: doctorId,
          deputy_id: deputyId,
          starts_on: startsOn,
          ends_on: endsOn,
          basis: basis.trim(),
        },
      });
    }
  };

  return (
    <Panel>
      <form onSubmit={submit} className="flex flex-col gap-3" aria-label="Назначить заместителя">
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-sm">
            Врач
            <select
              className={FIELD}
              value={doctorId}
              onChange={(e) => setDoctorId(e.target.value)}
            >
              <option value="">выберите врача</option>
              {doctors.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.full_name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Заместитель
            <select
              className={FIELD}
              value={deputyId}
              onChange={(e) => setDeputyId(e.target.value)}
            >
              <option value="">выберите заместителя</option>
              {doctors
                .filter((d) => d.id !== doctorId)
                .map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.full_name}
                  </option>
                ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            С
            <input
              className={FIELD}
              type="date"
              value={startsOn}
              onChange={(e) => setStartsOn(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            По
            <input
              className={FIELD}
              type="date"
              value={endsOn}
              onChange={(e) => setEndsOn(e.target.value)}
            />
          </label>
        </div>
        <label className="flex flex-col gap-1 text-sm">
          Основание
          <input
            className={FIELD}
            placeholder="Приказ № 12 от 20.10.2026 или график отпусков"
            maxLength={500}
            value={basis}
            onChange={(e) => setBasis(e.target.value)}
          />
        </label>
        <div className="flex items-center gap-3">
          <Button type="submit" disabled={!ready || assign.isPending}>
            Назначить
          </Button>
          <span className="text-sm text-ink-muted">
            Обе даты входят в период, не длиннее 92 дней.
          </span>
        </div>
        {assign.isError && <Alert tone="error">{assignError(assign.error)}</Alert>}
      </form>
    </Panel>
  );
}
