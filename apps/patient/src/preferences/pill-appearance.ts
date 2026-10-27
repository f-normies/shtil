/**
 * Вид таблетки на шаге онбординга и в профиле (кадр «Пациент · 09»): черновик выбора
 * и его описание. Чистые функции, без React.
 */
import type { Schemas } from "@compass/api-client";

export type PillShape = NonNullable<Schemas["PillAppearance"]["shape"]>;
export type PillColor = NonNullable<Schemas["PillAppearance"]["color"]>;
export type PillDraft = { shape: PillShape | null; color: PillColor | null };

type AppearanceSource = {
  appearance: { shape: PillShape | null; color: PillColor | null } | null;
  default_appearance: { shape: PillShape | null; color: PillColor | null } | null;
};

export const SHAPE_LABELS: Record<PillShape, string> = {
  round: "Круглая",
  oval: "Овальная",
  capsule: "Капсула",
};

export const COLOR_LABELS: Record<PillColor, string> = {
  white: "Белая",
  sand: "Песочная",
  peach: "Персиковая",
  blue: "Голубая",
};

/** Цвета таблетки из палитры «Штиль»; белая получает контрастную обводку в разметке. */
export const COLOR_TINTS: Record<PillColor, string> = {
  white: "var(--palette-neutral-0)",
  sand: "var(--palette-sand-200)",
  peach: "var(--palette-peach-200)",
  blue: "var(--palette-brand-200)",
};

/** Черновик — выбор пациента, иначе вид из инструкции, иначе пусто. */
export function pillDraftFor(item: AppearanceSource): PillDraft {
  const source = item.appearance ?? item.default_appearance;
  return { shape: source?.shape ?? null, color: source?.color ?? null };
}

export function pillDraftReady(draft: PillDraft): draft is { shape: PillShape; color: PillColor } {
  return draft.shape !== null && draft.color !== null;
}

export function pillDraftChanged(item: AppearanceSource, draft: PillDraft): boolean {
  const current = pillDraftFor(item);
  return current.shape !== draft.shape || current.color !== draft.color;
}

/** «Белая круглая» — цвет, потом форма, как в сводке кадра 11. */
export function describeAppearance(
  appearance: { shape: PillShape | null; color: PillColor | null } | null,
): string {
  if (!appearance?.shape || !appearance.color) return "Вид не выбран";
  const shape = SHAPE_LABELS[appearance.shape];
  return `${COLOR_LABELS[appearance.color]} ${shape.toLowerCase()}`;
}
