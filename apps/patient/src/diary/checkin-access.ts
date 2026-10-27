export type CheckInEntryMode = "new" | "draft" | "already-saved";

/**
 * Что открывает /check-in (P2-21): незавершённый черновик продолжается (в том числе опрос
 * после сохранённой отметки); сохранённая отметка без черновика закрывает повторную отметку.
 */
export function checkInEntryMode(input: {
  hasDailyEntryToday: boolean;
  hasDailyDraft: boolean;
}): CheckInEntryMode {
  if (input.hasDailyDraft) return "draft";
  return input.hasDailyEntryToday ? "already-saved" : "new";
}

/** Настроение из `?mood=`: целое 1–5, числом или строкой; остальное — без предвыбора. */
export function parseMoodSearch(value: unknown): number | undefined {
  const mood = typeof value === "number" ? value : typeof value === "string" ? Number(value) : NaN;
  return Number.isInteger(mood) && mood >= 1 && mood <= 5 ? mood : undefined;
}
