import { $api, type Schemas } from "@compass/api-client";
import { Button } from "@compass/ui";
import { type RefObject, useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import {
  answeredText,
  claimSource,
  QUESTION_TEXT,
  type QuestionView,
  questionBody,
  questionView,
} from "../../card/question";
import { initialFocus } from "../inspector/focus";
import { ClaimItem } from "./ClaimItem";
import { Asked, Caption, Notice, QuestionForm, SectionView, Skeleton } from "./parts";

type Claim = Schemas["Claim"];
type Section = { claim: Claim; trigger: HTMLElement };

const ANSWERS = "/api/v1/knowledge/answers";

/**
 * Вопрос к базе знаний в инспекторе (спека #108, 3.3; кадры «Врач v4 · 10в» и «10г»).
 * Вопрос, ответ и открытый раздел живут, пока открыт инспектор: закрытие и уход со страницы
 * их сбрасывают. Цитата открывает текст раздела здесь же; ответ при этом скрыт и не теряется.
 */
export function QuestionPanel() {
  const [draft, setDraft] = useState("");
  const [answeredAt, setAnsweredAt] = useState(0);
  const [section, setSection] = useState<Section | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const backRef = useRef<HTMLButtonElement>(null);
  // Без `source_ids`: поиск по всему корпусу. Данных пациента в запросе нет (спека, 3.2).
  const ask = $api.useMutation("post", ANSWERS, { onSuccess: () => setAnsweredAt(Date.now()) });
  const view = questionView({
    status: ask.status,
    question: ask.variables?.body.question,
    data: ask.data,
  });
  useRescuedFocus(rootRef, view.kind);

  const submit = () => {
    const body = questionBody(draft);
    if (body && !view.locked) {
      setDraft("");
      ask.mutate({ body });
    }
  };
  const retry = () => view.asked && ask.mutate({ body: { question: view.asked } });
  const openSection = (claim: Claim, trigger: HTMLElement) => {
    flushSync(() => setSection({ claim, trigger }));
    backRef.current?.focus();
  };
  const closeSection = () => {
    flushSync(() => setSection(null));
    section?.trigger.focus();
  };

  const form = (
    <QuestionForm
      draft={draft}
      locked={view.locked}
      canSend={questionBody(draft) !== null}
      onDraft={setDraft}
      onSubmit={submit}
    />
  );
  return (
    <div ref={rootRef}>
      {section && <SectionView claim={section.claim} backRef={backRef} onBack={closeSection} />}
      <div hidden={section !== null} className="flex flex-col gap-4">
        {view.asked && <Asked question={view.asked} />}
        {/* Область есть всегда: читалка объявляет ожидание, ответ и ошибку. */}
        <div aria-live="polite">
          <Reply view={view} answeredAt={answeredAt} onOpen={openSection} />
        </div>
        {view.retry && (
          <Button variant="secondary" className="self-start" data-autofocus onClick={retry}>
            Повторить
          </Button>
        )}
        {view.intro ? (
          <>
            {form}
            <Caption />
          </>
        ) : (
          <>
            <Caption />
            {view.form && form}
          </>
        )}
      </div>
    </div>
  );
}

function Reply({
  view,
  answeredAt,
  onOpen,
}: {
  view: QuestionView;
  answeredAt: number;
  onOpen: (claim: Claim, trigger: HTMLElement) => void;
}) {
  if (view.kind === "waiting") {
    return (
      <div className="flex flex-col gap-4">
        <p className="text-[15px] leading-5 font-semibold text-ink">{view.notice}</p>
        <Skeleton />
      </div>
    );
  }
  if (view.kind === "empty" || view.kind === "error") {
    return <Notice kind={view.kind}>{view.notice}</Notice>;
  }
  if (view.kind === "field") {
    return <p className="text-[15px] leading-[22px] text-ink-muted">{QUESTION_TEXT.intro}</p>;
  }
  return (
    <div className="flex flex-col gap-1 animate-fade-in">
      <p className="text-[13px] leading-[18px] text-ink-muted">{answeredText(answeredAt)}</p>
      <ul className="flex flex-col">
        {view.claims.map((claim, i) => (
          // Утверждения могут повторяться, а список меняется только целиком: ключ — номер.
          // biome-ignore lint/suspicious/noArrayIndexKey: список не переставляется
          <li key={i}>
            {i > 0 && <div aria-hidden className="h-px bg-separator" />}
            <ClaimItem
              compact
              claim={claim}
              source={claimSource(claim)}
              action="открыть текст раздела"
              onOpen={(trigger) => onOpen(claim, trigger)}
            />
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Поле или кнопку, где был фокус, убрали или отключили (ожидание, ошибка): фокус ушёл
 * на страницу. Тогда он возвращается на главное действие нового состояния — поле вопроса
 * или «Повторить». Прокрутка не сдвигается: ответ остаётся на виду.
 */
function useRescuedFocus(rootRef: RefObject<HTMLDivElement | null>, kind: QuestionView["kind"]) {
  // biome-ignore lint/correctness/useExhaustiveDependencies: проверка после смены состояния
  useEffect(() => {
    const active = document.activeElement;
    if (active && active !== document.body) {
      return;
    }
    const root = rootRef.current;
    if (root) {
      initialFocus<HTMLElement>(root)?.focus({ preventScroll: true });
    }
  }, [kind, rootRef]);
}
