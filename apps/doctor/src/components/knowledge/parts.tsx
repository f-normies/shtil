/**
 * Части панели вопроса к базе знаний (спека #108, 3.3; кадры «Врач v4 · 10в» и «10г»):
 * вопрос над ответом, пометки, подпись справки, поле вопроса, скелетон и текст раздела.
 */
import type { Schemas } from "@compass/api-client";
import { Button, TextField } from "@compass/ui";
import { ChevronLeft, CircleAlert, Info } from "lucide-react";
import type { ReactNode, RefObject } from "react";
import { QUESTION_MAX, QUESTION_TEXT, sendsOnKey } from "../../card/question";
import { claimBasis } from "../../card/sources";
import { SourceInspector } from "../source/SourceInspector";

const ICON = "size-5 shrink-0";

/** «Ваш вопрос»: отправленный вопрос остаётся вверху, пока готовится и показан ответ. */
export function Asked({ question }: { question: string }) {
  return (
    <div className="flex flex-col gap-0.5 [overflow-wrap:anywhere]">
      <p className="text-[13px] leading-[18px] text-ink-muted">{QUESTION_TEXT.asked}</p>
      <p className="text-[15px] leading-[22px] whitespace-pre-line text-ink">{question}</p>
    </div>
  );
}

/** Пустой ответ и ошибка: значок и текст, цвет не единственный носитель смысла. */
export function Notice({ kind, children }: { kind: "empty" | "error"; children: ReactNode }) {
  const Icon = kind === "error" ? CircleAlert : Info;
  return (
    <p className="flex items-start gap-2 text-[15px] leading-5 font-semibold text-ink">
      <Icon aria-hidden className={ICON} strokeWidth={1.75} />
      {children}
    </p>
  );
}

/** Подпись справки — во всех состояниях. */
export function Caption() {
  return (
    <p className="flex items-start gap-2 text-[13px] leading-[18px] text-ink-muted">
      <Info aria-hidden className={`${ICON} text-ink`} strokeWidth={1.75} />
      {QUESTION_TEXT.caption}
    </p>
  );
}

/**
 * Поле вопроса до 1000 знаков и «Спросить». Enter отправляет, Shift+Enter переносит строку.
 * Пока готовится ответ, поле и кнопка недоступны.
 */
export function QuestionForm({
  draft,
  locked,
  canSend,
  onDraft,
  onSubmit,
}: {
  draft: string;
  locked: boolean;
  canSend: boolean;
  onDraft: (draft: string) => void;
  onSubmit: () => void;
}) {
  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <TextField
        multiline
        data-autofocus
        label={QUESTION_TEXT.label}
        placeholder={QUESTION_TEXT.placeholder}
        hint={QUESTION_TEXT.hint}
        maxLength={QUESTION_MAX}
        rows={3}
        value={draft}
        disabled={locked}
        onChange={(event) => onDraft(event.target.value)}
        onKeyDown={(event) => {
          const { key, shiftKey, nativeEvent } = event;
          const { isComposing, keyCode } = nativeEvent;
          if (sendsOnKey({ key, shiftKey, isComposing, keyCode })) {
            event.preventDefault();
            onSubmit();
          }
        }}
      />
      <Button type="submit" className="self-start" disabled={locked || !canSend}>
        Спросить
      </Button>
    </form>
  );
}

/** Строки-заглушки ответа, как «Место для цитаты» в кадре 10. */
export function Skeleton() {
  const lines = [
    ["w-full", "w-11/12", "w-[96%]", "w-4/5", "w-1/2"],
    ["w-full", "w-[88%]", "w-3/4"],
  ];
  return (
    <div aria-hidden className="flex flex-col gap-4 pt-1">
      {lines.map((group) => (
        <div key={group.join()} className="flex flex-col gap-2">
          {group.map((width) => (
            <div
              key={width}
              className={`h-2 rounded bg-surface-sunken ${width} motion-safe:animate-pulse`}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

/**
 * Текст раздела по цитате: фрагмент целиком с выделенной цитатой, как у утверждения справки
 * в инспекторе источника (#106). «‹ Ответ» возвращает к ответу. Подпись справки и здесь.
 */
export function SectionView({
  claim,
  backRef,
  onBack,
}: {
  claim: Schemas["Claim"];
  backRef: RefObject<HTMLButtonElement | null>;
  onBack: () => void;
}) {
  return (
    <div className="flex flex-col gap-4 animate-fade-in">
      <button
        ref={backRef}
        type="button"
        data-autofocus
        onClick={onBack}
        className="-ml-1 inline-flex min-h-9 items-center gap-0.5 self-start rounded-md pr-2 text-[15px] leading-5 font-semibold text-primary-text hover:bg-primary-soft focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
      >
        <ChevronLeft aria-hidden className={ICON} strokeWidth={1.75} />
        Ответ
      </button>
      <SourceInspector basis={claimBasis(claim, [])} />
      <Caption />
    </div>
  );
}
