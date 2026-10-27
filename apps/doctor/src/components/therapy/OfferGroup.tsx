import { $api, type Schemas } from "@compass/api-client";
import { Alert, Button, StatusIcon } from "@compass/ui";
import { useQueryClient } from "@tanstack/react-query";
import { BookOpen, CircleX, Clock, Info, Moon, Pill, Sun, Sunrise } from "lucide-react";
import { type Ref, useId, useState } from "react";
import { detailOf } from "../../card/errors";
import { refreshTherapy } from "../../card/queries";
import { courseBar, doseSlots, failedText, tabletsText } from "../../card/regimen";
import { instructionBasis, refsText, regimenBasis } from "../../card/sources";
import { todayIso } from "../../card/therapy";
import { PROPOSAL_OUTLINE, Section } from "../signals/Section";
import { SourceButton } from "../source/SourceButton";
import { CheckRows } from "./CheckRows";
import { therapyError } from "./errors";
import { ReviseSheet } from "./ReviseSheet";

type Option = Schemas["OptionOut"];

const SLOT_ICON: Record<string, typeof Sun> = {
  Утро: Sunrise,
  День: Sun,
  Вечер: Moon,
  "На ночь": Moon,
};

/**
 * Предложение назначения по кадру «Врач v4 · 10» (спека #28, 8.2–8.4): препарат,
 * показание, схема «Утро / Вечер» и указания с источником, полоса курса, проверки и
 * решение врача. Без предложения (проверка не пройдена) — «Назначить нельзя» с причиной,
 * обхода нет. `onDecided` — решение сохранено или уже принято раньше: экран забывает
 * ответ подбора и показывает свежие данные. `headingRef` — заголовок секции для прокрутки
 * и фокуса после подбора (M-5).
 */
export function OfferGroup({
  offer,
  onDecided,
  headingRef,
}: {
  offer: Option;
  onDecided: (notice: string) => void;
  headingRef?: Ref<HTMLHeadingElement>;
}) {
  const proposal = offer.proposal;
  return (
    <>
      {proposal ? (
        <Section title="Предложение назначения" flush headingRef={headingRef}>
          <div className={`rounded-xl ${PROPOSAL_OUTLINE}`}>
            <OfferBody offer={offer} />
          </div>
        </Section>
      ) : (
        <Section title="Назначить нельзя" headingRef={headingRef}>
          <p className="flex gap-3 rounded-lg bg-danger-soft px-3 py-2 text-[15px] text-danger">
            <CircleX aria-hidden className="mt-0.5 size-5 shrink-0" />
            <span>
              <span className="font-semibold">
                {offer.trade_name}, {offer.indication_title}.
              </span>{" "}
              Не пройдено: {failedText(offer.checks)}. Назначить через систему нельзя.
            </span>
          </p>
        </Section>
      )}
      <CheckRows checks={offer.checks} />
      {proposal && <Decision offer={offer} proposalId={proposal.id} onDecided={onDecided} />}
    </>
  );
}

function OfferBody({ offer }: { offer: Option }) {
  const r = offer.regimen;
  const basis = regimenBasis(offer);
  const bar = courseBar(r.duration_days, r.max_days, todayIso());
  const codes = offer.indication_icd10.join(", ");
  const slots = doseSlots(r.times_per_day, r.schedule);
  // Расписание не разложилось на «Утро / Вечер» — его текст из карточки показан отдельно.
  const unnamed = slots[0]?.startsWith("Приём");
  return (
    <div className="flex flex-col">
      <div className="flex flex-col gap-1 p-4">
        <p className="flex items-center justify-between gap-3">
          <span className="flex items-center gap-2 text-[15px] font-semibold text-primary-text">
            <StatusIcon status="proposed" decorative />
            Предложение системы
          </span>
          <span className="text-[13px] text-ink-muted">ждёт подтверждения врача</span>
        </p>
        <h3 className="pt-2 text-xl leading-[26px] font-semibold text-ink">{offer.trade_name}</h3>
        <p className="text-[15px] text-ink-muted">
          {offer.inn}, {r.form}
        </p>
        <p className="text-[15px] text-ink">
          Показание по инструкции: {offer.indication_title}
          {codes && `, ${codes}`}
        </p>
      </div>

      <div className="mx-4 flex flex-col gap-2 border-t border-separator py-4">
        <p className="text-[13px] text-ink-muted">Схема приёма</p>
        <ul className="flex flex-col gap-2">
          {slots.map((slot) => {
            const Icon = SLOT_ICON[slot] ?? Pill;
            return (
              <li key={slot} className="flex items-center gap-3 text-[15px]">
                <Icon aria-hidden className="size-5 text-ink-muted" />
                <span className="flex-1">{slot}</span>
                <span className="font-semibold">{tabletsText(r.dose_mg, r.strength_mg)}</span>
              </li>
            );
          })}
          {unnamed && (
            <li className="flex items-center gap-3 text-[15px]">
              <Clock aria-hidden className="size-5 text-ink-muted" />
              Время приёма: {r.schedule}
            </li>
          )}
          {offer.instructions.map((i) => (
            <li key={i.text} className="flex items-center gap-3 text-[15px]">
              <Clock aria-hidden className="size-5 text-ink-muted" />
              <span className="flex-1">{i.text}</span>
              <span className="text-xs text-ink-muted">
                <SourceButton basis={instructionBasis(offer, i)}>{i.source.locator}</SourceButton>
              </span>
            </li>
          ))}
        </ul>
        <div className="flex w-fit items-center gap-2 text-[13px] font-semibold text-primary-text underline-offset-2 hover:underline">
          <BookOpen aria-hidden className="size-4" />
          <SourceButton basis={basis}>{refsText(basis.refs)}</SourceButton>
        </div>
      </div>

      <div className="mx-4 flex flex-col gap-2 border-t border-separator py-4">
        <p className="text-[13px] text-ink-muted">Длительность курса</p>
        <p className="text-[15px] font-semibold text-ink">{r.duration_text}</p>
        {bar && (
          <div role="img" aria-label={bar.spoken} className="flex flex-col gap-1 pt-1">
            <div className="flex items-center">
              <span
                className="h-1.5 rounded-full bg-primary"
                style={{ width: `${bar.share * 100}%` }}
              />
              {bar.maxLabel && (
                <span className="ml-1 h-0 flex-1 border-t-2 border-dashed border-primary-text" />
              )}
            </div>
            <p className="relative h-4 text-xs text-ink-muted">
              <span className="absolute left-0">{bar.startLabel}</span>
              {/* Дата срока — под концом сплошной части, дата максимума — у правого края. */}
              <span
                className="absolute -translate-x-full whitespace-nowrap"
                style={{ left: `${bar.share * 100}%` }}
              >
                {bar.endLabel}
              </span>
              {bar.maxLabel && <span className="absolute right-0">{bar.maxLabel}</span>}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * «Подтвердить назначение», «Изменить схему», «Отклонить» с причиной (спека #28, 8.4).
 * Решение, принятое в другой вкладке (409), тоже обновляет экран.
 */
function Decision({
  offer,
  proposalId,
  onDecided,
}: {
  offer: Option;
  proposalId: string;
  onDecided: (notice: string) => void;
}) {
  const queryClient = useQueryClient();
  const rejectId = useId();
  const [revising, setRevising] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const done = async (notice: string) => {
    await refreshTherapy(queryClient);
    onDecided(notice);
  };
  const onError = (error: unknown) => {
    if (detailOf(error) === "Proposal is already decided") {
      void done("Предложение уже рассмотрено");
    }
  };
  const confirm = $api.useMutation("post", "/api/v1/proposals/{proposal_id}/confirm", {
    onSuccess: () => done("Назначение подтверждено"),
    onError,
  });
  const reject = $api.useMutation("post", "/api/v1/proposals/{proposal_id}/reject", {
    onSuccess: () => done("Предложение отклонено"),
    onError,
  });
  const busy = confirm.isPending || reject.isPending;
  const error = confirm.error ?? reject.error;
  const path = { params: { path: { proposal_id: proposalId } } };

  return (
    <div className="flex flex-col gap-2">
      {error && <Alert tone="error">{therapyError(error)}</Alert>}
      {rejecting ? (
        <div className="flex flex-col gap-2">
          <label className="flex flex-col gap-1 text-sm">
            Причина отклонения, обязательно
            <textarea
              // biome-ignore lint/a11y/noAutofocus: поле появляется по нажатию «Отклонить».
              autoFocus
              className="min-h-16 rounded-lg border border-line bg-surface px-3 py-2"
              maxLength={2000}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          <div className="flex gap-2">
            <Button
              disabled={busy || !reason.trim()}
              onClick={() => reject.mutate({ ...path, body: { comment: reason.trim() } })}
            >
              Отклонить предложение
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                setRejecting(false);
                // Фокус — обратно на «Отклонить», которая вернулась на место.
                requestAnimationFrame(() => document.getElementById(rejectId)?.focus());
              }}
            >
              Отмена
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <Button disabled={busy} onClick={() => confirm.mutate({ ...path, body: {} })}>
            Подтвердить назначение
          </Button>
          <Button variant="link" disabled={busy} onClick={() => setRevising(true)}>
            Изменить схему
          </Button>
          <Button id={rejectId} variant="link" disabled={busy} onClick={() => setRejecting(true)}>
            Отклонить
          </Button>
        </div>
      )}
      <p className="flex items-center gap-2 text-[13px] text-ink-muted">
        <Info aria-hidden className="size-4" />
        Схема вне инструкции — только с обоснованием
      </p>
      {revising && (
        <ReviseSheet
          proposalId={proposalId}
          label={offer.regimen}
          onClose={() => setRevising(false)}
          onDecided={() => done("Назначение по вашей схеме сохранено")}
        />
      )}
    </div>
  );
}
