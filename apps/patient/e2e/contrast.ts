/** Контраст по WCAG 2.2 для браузерных тестов: цвета CSS вида `rgb(…)` и `rgba(…)`. */

export type Rgba = [number, number, number, number];

/** Каналы 0–255 и прозрачность 0–1 из `rgb(…)`, `rgba(…)` или `#rrggbb`. */
export function parseColor(color: string): Rgba {
  const hex = /^#([0-9a-f]{6})$/i.exec(color.trim())?.[1];
  if (hex) {
    const n = Number.parseInt(hex, 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255, 1];
  }
  const channels = color.match(/\d+(?:\.\d+)?/g)?.map(Number);
  if (!channels || channels.length < 3) throw new Error(`Unexpected CSS color: ${color}`);
  const [r = 0, g = 0, b = 0, a = 1] = channels;
  return [r, g, b, a];
}

/** Цвет `top` поверх непрозрачного `bottom`. */
export function over(top: Rgba, bottom: Rgba): Rgba {
  const a = top[3];
  return [
    top[0] * a + bottom[0] * (1 - a),
    top[1] * a + bottom[1] * (1 - a),
    top[2] * a + bottom[2] * (1 - a),
    1,
  ];
}

/** Фон под элементом: слои от ближнего к дальнему накладываются на белый холст. */
export function flatten(layers: readonly string[]): Rgba {
  let result: Rgba = [255, 255, 255, 1];
  for (const layer of [...layers].reverse()) result = over(parseColor(layer), result);
  return result;
}

export function luminance(color: string | Rgba): number {
  const channels = typeof color === "string" ? parseColor(color) : color;
  const linear = channels.slice(0, 3).map((channel) => {
    const normalized = channel / 255;
    return normalized <= 0.04045 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * (linear[0] ?? 0) + 0.7152 * (linear[1] ?? 0) + 0.0722 * (linear[2] ?? 0);
}

/** Коэффициент контраста двух цветов. Полупрозрачный передний цвет сначала ложится на фон. */
export function contrastRatio(foreground: string | Rgba, background: string | Rgba): number {
  const back = typeof background === "string" ? parseColor(background) : background;
  const fore = typeof foreground === "string" ? parseColor(foreground) : foreground;
  const lighter = Math.max(luminance(over(fore, back)), luminance(back));
  const darker = Math.min(luminance(over(fore, back)), luminance(back));
  return (lighter + 0.05) / (darker + 0.05);
}
