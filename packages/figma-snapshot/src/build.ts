/**
 * Клон кадра: `reference.tsx` → статический `index.html`. React рендерит разметку на сервере,
 * Tailwind CLI собирает стили только по коду кадра. Шрифт Onest лежит data URI в общем
 * `_shared/fonts.css`: страница открывается с диска, а Chrome не грузит шрифты по file://.
 */
import { execFile } from "node:child_process";
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { promisify } from "node:util";

import { build } from "esbuild";
import { PNG } from "pngjs";

import type { FrameEntry } from "./manifest.ts";
import { clonePage, tailwindInput } from "./page.ts";
import { type BoardItem, variantBoard } from "./variants.ts";

const run = promisify(execFile);
const PACKAGE = path.resolve(import.meta.dirname, "..");
const CACHE = path.join(PACKAGE, ".cache", "build");
const TAILWIND = path.join(PACKAGE, "node_modules", ".bin", "tailwindcss");
const FONTS = path.join(PACKAGE, "node_modules", "@fontsource-variable", "onest");

/** Общий `_shared/fonts.css`: Onest Variable под именем «Onest», как в макете. */
export async function writeFonts(out: string): Promise<void> {
  const css = await readFile(path.join(FONTS, "index.css"), "utf8");
  const files = [...css.matchAll(/url\(\.\/files\/([^)]+)\)/g)].map((match) => match[1] ?? "");
  let inlined = css.replaceAll("'Onest Variable'", "'Onest'");
  for (const file of new Set(files)) {
    const data = (await readFile(path.join(FONTS, "files", file))).toString("base64");
    inlined = inlined.replaceAll(`url(./files/${file})`, `url(data:font/woff2;base64,${data})`);
  }
  await mkdir(path.join(out, "_shared"), { recursive: true });
  const header =
    "/* Onest Variable из @fontsource-variable/onest (SIL OFL 1.1). Сгенерировано figma-snapshot. */\n";
  await writeFile(path.join(out, "_shared", "fonts.css"), header + inlined);
}

/** Цвет левого верхнего пикселя эталона: фон набора вариантов, которого нет в коде MCP. */
export async function cornerColor(png: string): Promise<string> {
  const image = PNG.sync.read(await readFile(png));
  const hex = [...image.data.subarray(0, 3)]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
  return `#${hex}`;
}

async function renderMarkup(entry: string, key: string): Promise<string> {
  const result = await build({
    stdin: {
      contents: [
        `import Frame from ${JSON.stringify(entry)};`,
        'import { createElement } from "react";',
        'import { renderToStaticMarkup } from "react-dom/server";',
        "export const markup = renderToStaticMarkup(createElement(Frame));",
      ].join("\n"),
      resolveDir: PACKAGE,
      loader: "tsx",
    },
    bundle: true,
    platform: "node",
    format: "esm",
    jsx: "automatic",
    external: ["react", "react/*", "react-dom", "react-dom/*"],
    write: false,
    logLevel: "silent",
  });
  const module = path.join(CACHE, `${key}.mjs`);
  await writeFile(module, result.outputFiles[0]?.text ?? "");
  try {
    const { markup } = (await import(pathToFileURL(module).href)) as { markup: string };
    return markup;
  } finally {
    await rm(module, { force: true });
  }
}

async function compileCss(reference: string, key: string): Promise<string> {
  const input = path.join(CACHE, `${key}.css`);
  const output = path.join(CACHE, `${key}.out.css`);
  await writeFile(input, tailwindInput(path.relative(CACHE, reference)));
  await run(TAILWIND, ["--input", input, "--output", output, "--minify"], { cwd: PACKAGE });
  const css = await readFile(output, "utf8");
  await rm(input, { force: true });
  await rm(output, { force: true });
  return css;
}

export interface VariantLayout {
  readonly background: string;
  readonly items: readonly BoardItem[];
}

/** Собирает `index.html` кадра из его `reference.tsx`; набор вариантов — доской вариантов. */
export async function buildClone(
  out: string,
  frame: FrameEntry,
  variants?: VariantLayout,
): Promise<void> {
  await mkdir(CACHE, { recursive: true });
  const dir = path.join(out, frame.dir);
  const reference = path.join(dir, "reference.tsx");
  const key = frame.dir.replaceAll("/", "__");
  let entry = reference;
  if (variants) {
    entry = path.join(CACHE, `${key}.board.tsx`);
    await writeFile(
      entry,
      variantBoard(reference, variants.background, frame.width, frame.height, variants.items),
    );
  }
  const [markup, css] = await Promise.all([renderMarkup(entry, key), compileCss(reference, key)]);
  if (variants) await rm(entry, { force: true });
  const html = clonePage({
    title: frame.name,
    nodeId: frame.id,
    width: frame.width,
    height: frame.height,
    css,
    markup,
    fontsHref: `${path.relative(dir, path.join(out, "_shared"))}/fonts.css`,
  });
  await writeFile(path.join(dir, "index.html"), html);
}
