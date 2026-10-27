import { useState } from "react";

// Длинная цитата — до 6 строк и «Показать полностью» (кадры 04 и 07б). Строк без замера
// не сосчитать: длиннее этого числа знаков цитата почти наверняка не помещается
// (как у фактов истории болезни, `FactRow`).
const LONG_QUOTE = 360;

/**
 * «Цитата пациента» (компонент «Цитата пациента»): облачко со словами, размеченное как
 * цитата (`blockquote`), с подписью источника.
 */
export function Quote({ text, caption }: { text: string; caption: string }) {
  const [full, setFull] = useState(false);
  const long = text.length > LONG_QUOTE;
  return (
    <figure className="flex max-w-[520px] flex-col gap-1.5">
      <blockquote
        className={`rounded-2xl rounded-bl-sm bg-surface-sunken px-4 py-3 text-[15px] leading-[22px] text-ink ${long && !full ? "line-clamp-6" : ""}`}
      >
        {text}
      </blockquote>
      <figcaption className="px-1 text-xs leading-4 text-ink-muted">{caption}</figcaption>
      {long && (
        <button
          type="button"
          className="self-start px-1 text-sm font-medium text-primary-text underline underline-offset-2"
          onClick={() => setFull(!full)}
        >
          {full ? "Свернуть" : "Показать полностью"}
        </button>
      )}
    </figure>
  );
}
