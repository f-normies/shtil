import { $api, type Schemas } from "@compass/api-client";
import { Alert, Button } from "@compass/ui";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useId, useRef, useState } from "react";
import { detailOf } from "../../card/errors";
import { formatMomentInline } from "../../card/format";
import { refreshAlerts, refreshProposals, useAlertDecisions, useAlerts } from "../../card/queries";
import { arrivedMandatory, declineHint, routingSignals } from "../../card/referral";
import { alertBasis } from "../../card/sources";
import { LEVEL_ICON } from "../card/LevelBadge";
import { SourceLine } from "../source/SourceLine";
import { SignalReason } from "./ReasonBlock";
import { SnoozeButton } from "./SnoozeButton";

type AlertOut = Schemas["AlertOut"];

const ALREADY = "Сигнал уже рассмотрен";
// Набор сигналов маршрутизации изменился после открытия листа (409 сервиса, `referral.CHANGED`).
const CHANGED = "Сигналы изменились, проверьте лист";
const TONE = { mandatory: "text-danger", desirable: "text-warning" } as const;

/**
 * Лист «Направление к психиатру» (спека #35; кадры 11, 11б, 11в). Решение — по всем
 * нерешённым сигналам маршрутизации пациента, которые врач видит в листе. Esc и клик
 * по затемнению закрывают лист без решения. Набор устарел (409) — врач обновляет лист.
 */
export function ReferralSheet({
  patientId,
  seen: initial,
  onClose,
}: {
  patientId: string;
  seen: AlertOut[];
  onClose: () => void;
}) {
  const id = useId();
  const ref = useRef<HTMLDialogElement>(null);
  const commentRef = useRef<HTMLTextAreaElement>(null);
  const firstReasonRef = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();
  const decisions = useAlertDecisions();
  const [seen, setSeen] = useState(initial);
  const current = routingSignals(useAlerts("open", patientId).data ?? seen, patientId);
  const [mode, setMode] = useState<"confirm" | "decline">("confirm");
  const [comment, setComment] = useState("");
  const [reasonId, setReasonId] = useState<string | null>(null);
  const [declineText, setDeclineText] = useState("");
  const [snoozeOpen, setSnoozeOpen] = useState(false);
  // Закрытие через `close()`: <dialog> сам возвращает фокус на кнопку, открывшую лист.
  const close = () => ref.current?.close();
  const decide = $api.useMutation("post", "/api/v1/patients/{patient_id}/referral", {
    onSuccess: close,
    onSettled: () => Promise.all([refreshAlerts(queryClient), refreshProposals(queryClient)]),
  });
  const mandatory = seen.some((a) => a.level === "mandatory");
  const arrived = arrivedMandatory(
    seen.map((a) => a.id),
    current,
  );
  const hint = declineHint(reasonId, declineText);
  const rawDetail = detailOf(decide.error);
  // У 409 и 422 сервиса — текст для врача; у 422 проверки схемы — список полей.
  const detail =
    typeof rawDetail === "string"
      ? rawDetail
      : rawDetail !== null
        ? "Проверьте введённые данные"
        : null;
  const send = (body: { decision: "confirm" | "decline"; comment: string; reason_id?: string }) =>
    decide.mutate({
      params: { path: { patient_id: patientId } },
      body: { ...body, alert_ids: seen.map((a) => a.id) },
    });
  // Повтор после сбоя — с тем, что врач ввёл сейчас, а не в момент сбоя.
  const resend = () => {
    if (mode === "confirm") {
      send({ decision: "confirm", comment });
    } else if (!hint && reasonId) {
      send({ decision: "decline", comment: declineText, reason_id: reasonId });
    }
  };

  useEffect(() => {
    ref.current?.showModal();
  }, []);

  // «Отклонить с причиной»: фокус на первом варианте причины (кадр 11в).
  useEffect(() => {
    if (mode === "decline") {
      firstReasonRef.current?.focus();
    }
  }, [mode]);

  return (
    // biome-ignore lint/a11y/useKeyWithClickEvents: клавиатура закрывает <dialog> по Esc штатно, клик — только по затемнению
    <dialog
      ref={ref}
      aria-labelledby={`${id}-title`}
      aria-modal="true"
      onClose={onClose}
      onCancel={(e) => snoozeOpen && e.preventDefault()}
      // Затемнение — сам <dialog> без отступов: клик внутри листа попадает в его содержимое.
      onClick={(e) => e.target === ref.current && close()}
      className={`m-auto max-h-[90dvh] w-[688px] max-w-[calc(100%-2rem)] overflow-y-auto rounded-xl border-t-4 bg-surface p-0 text-ink shadow-xl backdrop:bg-black/40 ${mandatory ? "border-danger" : "border-warning"}`}
    >
      <div className="p-6">
        <header className="mb-4">
          <h2
            id={`${id}-title`}
            tabIndex={-1}
            className="text-xl font-semibold"
            onKeyDown={(e) => {
              // Первая остановка Tab после заголовка — поле ввода (кадры 11, 11в), хотя в DOM
              // раньше стоит ссылка «Открыть основание» в строке источника; она доступна
              // по Shift+Tab из поля.
              if (e.key === "Tab" && !e.shiftKey) {
                const first = mode === "confirm" ? commentRef.current : firstReasonRef.current;
                if (first) {
                  e.preventDefault();
                  first.focus();
                }
              }
            }}
          >
            Направление к психиатру
          </h2>
          <p className="text-ink-muted">Рассмотрите и зафиксируйте решение.</p>
        </header>

        {arrived.length > 0 && (
          <p role="status" className="mb-3 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
            Новый сигнал «Обязательно»:{" "}
            <Link
              className="underline"
              to="/alerts/$alertId"
              params={{ alertId: arrived[0]?.id ?? "" }}
              search={{}}
            >
              {arrived[0]?.title}
            </Link>
            . Закройте лист и откройте его снова, чтобы решить по всем сигналам.
          </p>
        )}

        <div className="flex flex-col gap-5">
          {seen.map((a) => {
            const Icon = LEVEL_ICON[a.level];
            const basis = alertBasis(a);
            return (
              <section key={a.id} className="flex flex-col gap-3">
                <p className="flex items-center justify-between">
                  <span className={`flex items-center gap-2 font-semibold ${TONE[a.level]}`}>
                    <Icon aria-hidden className="size-5" />
                    {a.level === "mandatory" ? "Обязательно" : "Желательно"}
                  </span>
                  <span className="text-sm text-ink-muted">
                    Сигнал от {formatMomentInline(a.last_triggered_at)}
                  </span>
                </p>
                <h3 className="text-lg font-semibold">{a.title}</h3>
                {basis && <SourceLine basis={basis} />}
                <SignalReason alert={a} />
              </section>
            );
          })}
        </div>

        {decide.isError && (
          <div className="mt-4 flex flex-col gap-2">
            <Alert tone="error">{detail ?? "Не удалось сохранить решение. Ввод сохранён."}</Alert>
            <div>
              {detail === ALREADY ? (
                <Button variant="secondary" onClick={close}>
                  Закрыть
                </Button>
              ) : detail === CHANGED ? (
                <Button
                  variant="secondary"
                  onClick={() => {
                    setSeen(current);
                    decide.reset();
                  }}
                >
                  Обновить лист
                </Button>
              ) : detail === null ? (
                <Button variant="secondary" onClick={resend}>
                  Повторить
                </Button>
              ) : null}
            </div>
          </div>
        )}

        {mode === "confirm" ? (
          <div className="mt-5 flex flex-col gap-4">
            <label className="flex flex-col gap-1.5 text-sm font-medium">
              Комментарий для психиатра
              <textarea
                ref={commentRef}
                className="min-h-11 rounded-md border border-line px-3 py-2 text-base font-normal [field-sizing:content] max-h-40"
                maxLength={2000}
                placeholder="Что важно знать психиатру: жалобы, терапия, вопросы"
                value={comment}
                onChange={(e) => setComment(e.target.value)}
              />
            </label>
            <div className="flex flex-wrap justify-end gap-2">
              <Button variant="secondary" onClick={() => setMode("decline")}>
                Отклонить с причиной
              </Button>
              {!mandatory && decisions.data?.snooze && (
                <SnoozeButton
                  onDone={close}
                  onOpenChange={setSnoozeOpen}
                  target={{ patientId, alertIds: seen.map((a) => a.id) }}
                  snooze={decisions.data.snooze}
                />
              )}
              <Button
                disabled={decide.isPending}
                onClick={() => send({ decision: "confirm", comment })}
              >
                Подтвердить направление
              </Button>
            </div>
          </div>
        ) : (
          <form
            className="mt-5 flex flex-col gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (!hint && reasonId) {
                send({ decision: "decline", comment: declineText, reason_id: reasonId });
              }
            }}
          >
            <fieldset className="flex flex-col divide-y divide-line">
              <legend className="mb-2 font-semibold">Почему не направляете</legend>
              {(decisions.data?.referral_decline ?? []).map((r, i) => (
                <label key={r.id} className="flex cursor-pointer items-center gap-3 py-2.5">
                  <input
                    ref={i === 0 ? firstReasonRef : undefined}
                    type="radio"
                    name={`${id}-reason`}
                    className="size-5 accent-primary"
                    checked={reasonId === r.id}
                    onChange={() => setReasonId(r.id)}
                  />
                  <span className={reasonId === r.id ? "font-semibold" : ""}>{r.text}</span>
                </label>
              ))}
            </fieldset>
            <label className="flex flex-col gap-1.5 text-sm font-medium">
              Комментарий, обязательно
              <textarea
                className="min-h-11 rounded-md border border-line px-3 py-2 text-base font-normal"
                maxLength={2000}
                placeholder="Что важно сохранить в карточке"
                aria-describedby={`${id}-hint`}
                value={declineText}
                onChange={(e) => setDeclineText(e.target.value)}
              />
            </label>
            <div className="flex flex-wrap items-center justify-end gap-2">
              <Button type="button" variant="secondary" onClick={() => setMode("confirm")}>
                Назад
              </Button>
              <Button type="submit" disabled={decide.isPending || hint !== null}>
                Отклонить направление
              </Button>
            </div>
            <p id={`${id}-hint`} role="status" className="text-right text-sm text-ink-muted">
              {hint ?? ""}
            </p>
          </form>
        )}
      </div>
    </dialog>
  );
}
