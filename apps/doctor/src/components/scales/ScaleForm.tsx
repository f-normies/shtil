import { $api, type Schemas } from "@compass/api-client";
import { Alert, Button } from "@compass/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { clinicianTotal } from "../../card/clinician";
import { shortScaleName } from "../../card/levels";
import { refreshAlerts, refreshAssessments } from "../../card/queries";
import { Sheet } from "../details/Sheet";

type Scale = Schemas["Scale"];

/** Причина конфликта из расширения RFC 9457: один статус 409 имеет несколько причин. */
function conflictCode(error: unknown): string | null {
  return typeof error === "object" &&
    error !== null &&
    "status" in error &&
    error.status === 409 &&
    "code" in error &&
    typeof error.code === "string"
    ? error.code
    : null;
}

function errorText(error: unknown): string {
  switch (conflictCode(error)) {
    case "scale_version_changed":
      return "Шкала обновилась. Загрузите новую версию и заполните её заново.";
    case "submission_conflict":
      return "Эта отправка уже сохранена с другими ответами. Закройте форму и проверьте историю шкал.";
    case "lead_doctor_missing":
      return "У пациента нет ведущего врача. Обратитесь к администратору.";
    default:
      return "Результат не сохранён. Проверьте связь и попробуйте ещё раз.";
  }
}

/**
 * Лист заполнения шкалы врача по интервью (спека #94, форма): пункты по порядку, все
 * варианты полными формулировками КР с баллом, итог по ходу. Черновика нет. Ключ отправки
 * создаётся при открытии листа: повторный клик не создаст второй результат.
 */
export function ScaleForm({
  scale,
  patientId,
  onClose,
  onReload,
}: {
  scale: Scale;
  patientId: string;
  onClose: () => void;
  onReload: () => Promise<void>;
}) {
  const id = useId();
  const queryClient = useQueryClient();
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [submissionId] = useState(() => crypto.randomUUID());
  const [reloading, setReloading] = useState(false);
  const [reloadFailed, setReloadFailed] = useState(false);
  const save = $api.useMutation("post", "/api/v1/patients/{patient_id}/assessments");
  const total = clinicianTotal(scale, answers);
  const name = shortScaleName(scale.title);
  const complete = total.sum !== null;
  const versionChanged = conflictCode(save.error) === "scale_version_changed";

  return (
    <Sheet title={`${name}: результат интервью`} description={scale.instruction} onClose={onClose}>
      {(close) => (
        <form
          className="flex flex-col gap-5"
          onSubmit={(e) => {
            e.preventDefault();
            if (!complete || save.isPending || versionChanged) return;
            save.mutate(
              {
                params: { path: { patient_id: patientId } },
                body: {
                  submission_id: submissionId,
                  scale_id: scale.id,
                  scale_version: scale.version,
                  answers,
                },
              },
              {
                onSuccess: async () => {
                  close();
                  await Promise.all([refreshAssessments(queryClient), refreshAlerts(queryClient)]);
                },
              },
            );
          }}
        >
          <ol className="flex flex-col gap-5">
            {scale.items.map((item, n) => (
              <li key={item.id}>
                <fieldset className="flex flex-col">
                  <legend className="mb-1 font-semibold">
                    {n + 1}. {item.text}
                  </legend>
                  {item.options.map((o) => (
                    <label
                      key={o.value}
                      className="flex cursor-pointer items-start gap-3 border-b border-separator py-2 last:border-b-0"
                    >
                      <input
                        type="radio"
                        name={`${id}-${item.id}`}
                        className="mt-0.5 size-5 shrink-0 accent-primary"
                        checked={answers[item.id] === o.value}
                        onChange={() => setAnswers((a) => ({ ...a, [item.id]: o.value }))}
                      />
                      <span
                        className={`flex-1 text-[15px] ${answers[item.id] === o.value ? "font-semibold" : ""}`}
                      >
                        {o.text}
                      </span>
                      <span className="w-6 shrink-0 text-right text-[15px] text-ink-muted tabular-nums">
                        {o.value}
                      </span>
                    </label>
                  ))}
                </fieldset>
              </li>
            ))}
          </ol>
          {save.error && (
            <Alert tone="error">
              {errorText(save.error)}
              {versionChanged && (
                <Button
                  type="button"
                  variant="secondary"
                  disabled={reloading}
                  onClick={async () => {
                    setReloading(true);
                    setReloadFailed(false);
                    try {
                      await onReload();
                    } catch {
                      setReloadFailed(true);
                    } finally {
                      setReloading(false);
                    }
                  }}
                >
                  Обновить шкалу
                </Button>
              )}
              {reloadFailed && (
                <p>Не удалось загрузить новую шкалу. Проверьте связь и повторите.</p>
              )}
            </Alert>
          )}
          <footer className="sticky bottom-0 -mx-6 -mb-6 flex items-center justify-between gap-4 border-t border-line bg-surface px-6 py-4">
            <p aria-live="polite" className="text-[15px] font-semibold">
              {complete
                ? `Сумма ${total.sum} из ${total.max}${total.band ? `, ${total.band.toLowerCase()}` : ""}`
                : `Отвечено ${total.answered} из ${total.total}`}
            </p>
            <div className="flex shrink-0 gap-2 whitespace-nowrap">
              <Button type="button" variant="secondary" onClick={close}>
                Отмена
              </Button>
              <Button type="submit" disabled={!complete || save.isPending || versionChanged}>
                Сохранить результат
              </Button>
            </div>
          </footer>
        </form>
      )}
    </Sheet>
  );
}
