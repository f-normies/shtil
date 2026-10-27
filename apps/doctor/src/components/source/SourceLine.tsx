import { Button } from "@compass/ui";
import { BookOpen } from "lucide-react";
import { useId } from "react";
import { type Basis, refsText } from "../../card/sources";
import { useOpenSource } from "./SourceInspector";

/**
 * Строка «Источник правила» (компонент «Врач v4/Источник правила», спека #106, 3.7):
 * правило с версией или проверка, документы с разделами и «Открыть основание» —
 * инспектор со всеми ссылками основания. Как в макете, блок занимает две строки:
 * правило и документы по одной с многоточием, полный текст — в инспекторе.
 */
export function SourceLine({ basis }: { basis: Basis }) {
  const openSource = useOpenSource();
  const id = useId();
  return (
    <div className="flex items-start gap-2 border-t border-separator pt-3">
      <BookOpen aria-hidden className="size-5 shrink-0 text-ink-subtle" strokeWidth={1.75} />
      <div id={id} className="flex min-w-0 flex-1 flex-col gap-0.5">
        <p className="truncate text-[13px] leading-[18px] text-ink-muted">{basis.heading}</p>
        <p className="truncate text-xs leading-4 text-ink-subtle">{refsText(basis.refs)}</p>
      </div>
      <Button
        variant="link"
        className="shrink-0"
        aria-describedby={id}
        onClick={(event) => openSource(basis, event.currentTarget)}
      >
        Открыть основание
      </Button>
    </div>
  );
}
