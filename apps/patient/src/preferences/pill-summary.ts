/** Значение строки «Вид таблеток» в профиле (кадр 29): «Авиандр: белая, круглая». */

type Appearance = {
  color: "white" | "sand" | "peach" | "blue" | null;
  shape: "round" | "oval" | "capsule" | null;
};

type Item = { trade_name: string; appearance: Appearance | null };

const colorLabels: Record<NonNullable<Appearance["color"]>, string> = {
  white: "белая",
  sand: "песочная",
  peach: "персиковая",
  blue: "голубая",
};

const shapeLabels: Record<NonNullable<Appearance["shape"]>, string> = {
  round: "круглая",
  oval: "овальная",
  capsule: "капсула",
};

export function pillAppearanceSummary(items: readonly Item[]): string {
  const parts = items.flatMap((item) => {
    const { color, shape } = item.appearance ?? {};
    if (!color || !shape) return [];
    return [`${item.trade_name}: ${colorLabels[color]}, ${shapeLabels[shape]}`];
  });
  return parts.length > 0 ? parts.join("; ") : "Не выбран";
}
