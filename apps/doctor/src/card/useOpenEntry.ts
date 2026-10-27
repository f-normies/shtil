/**
 * «Открыть запись» из графика или находки (спека #115, раздел 3.5): фильтр сбрасывается на
 * «Все», недостающие страницы догружаются, пока запись не найдётся (`locateEntry`), затем
 * строка прокручивается к центру, получает фокус и коротко подсвечивается. Записи нет —
 * `unavailable`: пациент её скрыл. `scope` — период списка: в другом периоде прежнее
 * «недоступна» не показывается.
 */
import type { Schemas } from "@compass/api-client";
import type { InfiniteData, UseInfiniteQueryResult } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { type DiaryEntry, entryAnchor, locateEntry } from "./diary";

type Entries = UseInfiniteQueryResult<InfiniteData<Schemas["DiaryDoctorEntryPage"]>, unknown>;
type Find = (entries: readonly DiaryEntry[]) => DiaryEntry | undefined;

const HIGHLIGHT_MS = 2000;

export function useOpenEntry(entries: Entries, scope: string, onStart: () => void) {
  const [reveal, setReveal] = useState<string | null>(null);
  const [highlighted, setHighlighted] = useState<string | null>(null);
  const [unavailableIn, setUnavailableIn] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);

  // Строка появляется после отрисовки: со сброшенным фильтром или новой страницей.
  useEffect(() => {
    const row = reveal ? document.getElementById(entryAnchor(reveal)) : null;
    if (!row) {
      return;
    }
    row.scrollIntoView({ block: "center" });
    row.focus({ preventScroll: true });
    setHighlighted(reveal);
    setReveal(null);
  });

  useEffect(() => {
    if (!highlighted) {
      return;
    }
    const timer = setTimeout(() => setHighlighted(null), HIGHLIGHT_MS);
    return () => clearTimeout(timer);
  }, [highlighted]);

  /** `at` — время записи, если оно известно: по нему видно, что список её уже прошёл. */
  async function open(find: Find, at: string | null) {
    onStart();
    setUnavailableIn(null);
    setSearching(true);
    try {
      let loaded = entries.data?.pages.flatMap((page) => page.items) ?? [];
      let hasNext = entries.hasNextPage;
      for (;;) {
        const step = locateEntry(loaded, hasNext, find, at);
        if (step.kind === "found") {
          setReveal(step.entryId);
          return;
        }
        if (step.kind === "missing") {
          setUnavailableIn(scope);
          return;
        }
        const next = await entries.fetchNextPage();
        // Сбой страницы показывает сам список: «Не удалось загрузить дневник».
        if (next.isError) {
          return;
        }
        loaded = next.data?.pages.flatMap((page) => page.items) ?? [];
        hasNext = next.hasNextPage;
      }
    } finally {
      setSearching(false);
    }
  }

  return { open, highlighted, unavailable: unavailableIn === scope, searching };
}
