import { type RefObject, useEffect } from "react";
import { isFindShortcut } from "./shortcuts";

/** ⌘F и Ctrl+F ставят фокус в поиск по ФИО вместо поиска браузера (кадры 02 и 03). */
export function useFindShortcut(input: RefObject<HTMLInputElement | null>): void {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!isFindShortcut(event) || !input.current) return;
      event.preventDefault();
      input.current.focus();
      input.current.select();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [input]);
}
