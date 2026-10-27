import { type ReactNode, type Ref, useId } from "react";

/** Пунктир неподтверждённого предложения системы: primary-text, 1 px (кадры v4). */
export const PROPOSAL_OUTLINE = "border border-dashed border-primary-text";

/**
 * Секция подробностей по макетам v4 («Врач v4/Секция»): подпись прописными, белая группа
 * со скруглением, необязательная сноска. Вид «Предложение» — группа неподтверждённых
 * предложений системы в пунктире. `flush` — строки сами задают поля и разделители.
 * `headingRef` — заголовок можно сфокусировать программно (`tabIndex=-1`).
 */
export function Section({
  title,
  note,
  aside,
  proposal = false,
  flush = false,
  id,
  headingRef,
  children,
}: {
  title: string;
  note?: string;
  /** Короткое значение справа от подписи, например «2 из 5». */
  aside?: ReactNode;
  proposal?: boolean;
  flush?: boolean;
  id?: string;
  headingRef?: Ref<HTMLHeadingElement>;
  children: ReactNode;
}) {
  const titleId = useId();
  return (
    <section id={id} aria-labelledby={titleId} className="flex scroll-mt-4 flex-col gap-2">
      <h2
        ref={headingRef}
        tabIndex={headingRef ? -1 : undefined}
        className="flex justify-between rounded-sm px-4 text-xs font-semibold tracking-wide text-ink-muted uppercase outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
      >
        <span id={titleId}>{title}</span>
        {aside && <span className="font-normal tracking-normal normal-case">{aside}</span>}
      </h2>
      <div
        className={`rounded-xl ${flush ? "" : "p-4"} bg-surface ${proposal ? PROPOSAL_OUTLINE : ""}`}
      >
        {children}
      </div>
      {note && <p className="px-4 text-xs text-ink-muted">{note}</p>}
    </section>
  );
}
