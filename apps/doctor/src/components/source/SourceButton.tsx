import type { ReactNode } from "react";
import type { Basis } from "../../card/sources";
import { useOpenSource } from "./SourceInspector";

/**
 * Документ и раздел внутри строки текста (проверка, схема, указание по инструкции):
 * открывает основание в инспекторе (спека #106, 3.6). Кнопка переносится, как текст.
 */
export function SourceButton({ basis, children }: { basis: Basis; children: ReactNode }) {
  const openSource = useOpenSource();
  return (
    <button
      type="button"
      onClick={(event) => openSource(basis, event.currentTarget)}
      className="cursor-pointer rounded-sm text-left text-primary underline underline-offset-2 hover:no-underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
    >
      {children}
    </button>
  );
}
