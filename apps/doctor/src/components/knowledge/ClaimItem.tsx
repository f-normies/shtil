import type { Schemas } from "@compass/api-client";
import { BookOpen } from "lucide-react";
import { useId, useState } from "react";
import { isLongQuote, levelsSpoken, levelsText } from "../../card/guidance";

type Claim = Schemas["Claim"];

/**
 * Утверждение с дословной цитатой (компонент «Врач v4/Утверждение с цитатой»; спеки #107,
 * #108): утверждение, цитата-кнопка, документ и раздел, у КР — УУР и УДД. Цитата открывает
 * текст источника, куда — решает `onOpen`. В колонке текста длинная цитата свёрнута до
 * 6 строк; в инспекторе (`compact`, 280 px) цитата целиком, подписи 12/16, как в кадре 10в.
 * `statements` — несколько утверждений с одной цитатой: цитата под ними одна.
 */
export function ClaimItem({
  claim,
  source,
  action,
  onOpen,
  compact = false,
  statements = [claim.statement],
}: {
  claim: Claim;
  statements?: readonly string[];
  /** Документ и раздел под цитатой. */
  source: string;
  /** Что делает цитата, для чтения с экрана: «открыть источник». */
  action: string;
  onOpen: (trigger: HTMLElement) => void;
  compact?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const sourceId = useId();
  const levels = levelsText(claim.fragment);
  const clamped = !compact && !expanded;

  return (
    <div className="flex gap-3 py-3">
      <BookOpen aria-hidden className="size-5 shrink-0 text-ink" strokeWidth={1.75} />
      <div className="flex min-w-0 flex-1 flex-col gap-1.5 [overflow-wrap:anywhere]">
        {statements.map((statement) => (
          <p key={statement} className="text-[15px] leading-[22px] text-ink">
            {statement}
          </p>
        ))}
        <button
          type="button"
          aria-describedby={sourceId}
          className={`rounded-sm text-left text-sm text-primary hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary ${clamped ? "line-clamp-6" : ""}`}
          onClick={(event) => onOpen(event.currentTarget)}
        >
          «{claim.quote}»<span className="sr-only"> — {action}</span>
        </button>
        {clamped && isLongQuote(claim.quote) && (
          <button
            type="button"
            className="self-start text-[13px] font-medium text-primary underline"
            onClick={() => setExpanded(true)}
          >
            Показать полностью
          </button>
        )}
        <div
          className={`text-ink-muted ${compact ? "text-xs leading-4" : "text-[13px] leading-[18px]"}`}
        >
          <p id={sourceId}>{source}</p>
          {levels && (
            <p>
              <span aria-hidden>{levels}</span>
              <span className="sr-only">{levelsSpoken(claim.fragment)}</span>
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
