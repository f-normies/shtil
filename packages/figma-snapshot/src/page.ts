/** Шаблоны: HTML-страница клона и входной CSS для Tailwind. */

export interface ClonePage {
  readonly title: string;
  readonly nodeId: string;
  readonly width: number;
  readonly height: number;
  readonly css: string;
  readonly markup: string;
  /** Относительный путь к общему `fonts.css` со шрифтом Onest. */
  readonly fontsHref: string;
}

const ESCAPES: Record<string, string> = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" };

/** Экранирует текст для вставки в HTML. */
export function escapeHtml(text: string): string {
  return text.replace(/[&<>"]/g, (char) => ESCAPES[char] ?? char);
}

/** Статическая страница: кадр ровно в размер макета, стили Tailwind внутри. */
export function clonePage(page: ClonePage): string {
  return [
    "<!doctype html>",
    '<html lang="ru">',
    "<head>",
    '<meta charset="utf-8">',
    `<title>${escapeHtml(page.title)} — клон макета</title>`,
    `<link rel="stylesheet" href="${page.fontsHref}">`,
    `<style>${page.css}</style>`,
    `<style>body{margin:0;background:#fff}#frame{width:${page.width}px;height:${page.height}px;position:relative;overflow:hidden}</style>`,
    "</head>",
    "<body>",
    `<div id="frame" data-node-id="${escapeHtml(page.nodeId)}">${page.markup}</div>`,
    "</body>",
    "</html>",
    "",
  ].join("\n");
}

/**
 * Tailwind сканирует только `reference.tsx` кадра. Цвета, скругления и тени темы Tailwind
 * сброшены: их имена (`--radius-md`) совпадают с переменными Figma, но значения другие.
 * Без темы срабатывают запасные значения из макета: `var(--radius-md,8px)` → 8px.
 */
export function tailwindInput(referencePath: string): string {
  return [
    '@import "tailwindcss" source(none);',
    `@source "${referencePath}";`,
    "@theme {",
    "  --color-*: initial;",
    "  --color-white: #fff;",
    "  --color-black: #000;",
    "  --radius-*: initial;",
    "  --shadow-*: initial;",
    "  --drop-shadow-*: initial;",
    '  --font-sans: "Onest", ui-sans-serif, system-ui, sans-serif;',
    "}",
    "",
  ].join("\n");
}
