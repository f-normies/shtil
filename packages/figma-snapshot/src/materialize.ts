/**
 * Ответы Figma MCP из журналов → файлы снимка в `docs/design/figma/`: код кадра, ресурсы,
 * скриншот, заметки, иконки, токены, описания компонентов. Файл перекачивается, только если
 * его адрес сменился (журнал скачиваний в `download.ts`). Неудачное скачивание попадает
 * в `problems` и не обрывает снимок.
 */
import { existsSync } from "node:fs";
import { mkdir, readdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";

import { type Collected, hasCode } from "./collect.ts";
import { componentsMarkdown, frameNotes } from "./docs.ts";
import { download } from "./download.ts";
import { slugify } from "./layout.ts";
import type { FrameEntry, PageNotes } from "./manifest.ts";
import {
  annotationMarkdown,
  type ComponentNote,
  parseComponents,
  parseTextStyles,
  type StyleNote,
} from "./notes.ts";
import { findMissingAssets, localizeAssets, type MissingAsset, sanitizeCode } from "./sanitize.ts";
import { tokensCss } from "./tokens.ts";

export interface MaterializedFrame extends FrameEntry {
  readonly files: { reference: boolean; clone: boolean; screenshot: boolean };
  readonly parts?: MaterializedFrame[];
}

export interface Materialized {
  readonly frames: MaterializedFrame[];
  /** Ресурсы без SVG: выгрузить `figma/svgs.js` и запустить снова. */
  readonly missing: MissingAsset[];
  readonly pageNotes: Map<string, string[]>;
  /** Неудачные скачивания: файл не обновлён. */
  readonly problems: string[];
}

export interface MaterializeOptions {
  readonly out: string;
  readonly fileKey: string;
  readonly collected: Collected;
  readonly frames: readonly FrameEntry[];
  readonly notes: readonly PageNotes[];
  /** Путь файла → адрес, с которого он скачан; обновляется по ходу. */
  readonly ledger: Map<string, string>;
  readonly fetch?: typeof fetch;
}

interface Context extends MaterializeOptions {
  readonly components: Map<string, ComponentNote>;
  readonly missing: MissingAsset[];
  readonly problems: string[];
  readonly listingNames: Map<string, string>;
}

const PARALLEL_DOWNLOADS = 8;
const SPARSE_NODE =
  /<\w+ id="([^"]+)" name="([^"]+)" x="[^"]+" y="[^"]+" width="([^"]+)" height="([^"]+)"/g;

async function inParallel<T>(items: readonly T[], run: (item: T) => Promise<void>): Promise<void> {
  const queue = [...items];
  const worker = async () => {
    for (let item = queue.shift(); item !== undefined; item = queue.shift()) await run(item);
  };
  await Promise.all(Array.from({ length: PARALLEL_DOWNLOADS }, worker));
}

async function fetchInto(context: Context, url: string, target: string): Promise<void> {
  try {
    await download(url, target, {
      ledger: context.ledger,
      ...(context.fetch ? { fetch: context.fetch } : {}),
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    context.problems.push(`${path.relative(context.out, target)}: ${message}`);
  }
}

/** Удаляет из `assets/` файлы, на которые код кадра больше не ссылается. */
async function pruneAssets(dir: string, keep: ReadonlySet<string>): Promise<void> {
  for (const name of await readdir(dir)) {
    if (!keep.has(name)) await rm(path.join(dir, name), { force: true });
  }
}

function referenceHeader(frame: FrameEntry): string {
  return [
    `// Эталонный код кадра «${frame.name}» (узел ${frame.id}) из Figma MCP get_design_context.`,
    "// Сгенерировано packages/figma-snapshot, руками не править. Это справка: в приложении",
    "// собираем экран из компонентов packages/ui и токенов, а не копируем разметку целиком.",
    "",
  ].join("\n");
}

/** Код кадра → reference.tsx и assets/. Возвращает стили и компоненты из ответа MCP. */
async function writeReference(context: Context, frame: FrameEntry, texts: readonly string[]) {
  const dir = path.join(context.out, frame.dir);
  const assets = path.join(dir, "assets");
  await mkdir(assets, { recursive: true });
  const { code, downloads } = localizeAssets(sanitizeCode(texts[0] ?? ""));
  const keep = new Set(downloads.map((item) => item.name));
  const definitions: string[] = [];
  const unresolved: string[] = [];
  for (const asset of findMissingAssets(code)) {
    const svg = context.collected.svgs.get(asset.nodeId);
    if (svg) {
      await writeFile(path.join(assets, `${asset.variable}.svg`), svg.svg);
      keep.add(`${asset.variable}.svg`);
      definitions.push(`const ${asset.variable} = "./assets/${asset.variable}.svg";`);
      continue;
    }
    definitions.push(`const ${asset.variable} = undefined; // нет в снимке: узел ${asset.nodeId}`);
    unresolved.push(`${asset.variable} (узел ${asset.nodeId})`);
    context.missing.push(asset);
  }
  const body = definitions.length > 0 ? `${definitions.join("\n")}\n${code}` : code;
  await writeFile(path.join(dir, "reference.tsx"), `${referenceHeader(frame)}${body}\n`);
  await pruneAssets(assets, keep);
  await inParallel(downloads, (item) => fetchInto(context, item.url, path.join(assets, item.name)));

  let styles: StyleNote[] = [];
  let components: ComponentNote[] = [];
  for (const text of texts.slice(1)) {
    if (text.startsWith("These styles")) styles = parseTextStyles(text);
    if (text.startsWith("Component descriptions")) components = parseComponents(text);
  }
  for (const component of components) context.components.set(component.nodeId, component);
  return { styles, components, unresolved };
}

function annotationsFor(context: Context, frameId: string): string[] {
  const ids = context.notes.flatMap((page) => page.byFrame[frameId] ?? []);
  return ids.map((id) => annotationNote(context, id));
}

function annotationNote(context: Context, id: string): string {
  const name = context.listingNames.get(id) ?? id;
  const body = annotationMarkdown(context.collected.annotationTexts.get(id) ?? []);
  return name === "Аннотация"
    ? body
    : `### ${name.replace(/^Аннотация( для разработки)? · /, "")}\n\n${body}`;
}

/** Части разреженного кадра: вложенные узлы, для которых есть код. */
function sparseParts(context: Context, frame: FrameEntry, texts: readonly string[]): FrameEntry[] {
  const parts: FrameEntry[] = [];
  for (const match of texts.join("\n").matchAll(SPARSE_NODE)) {
    const [, id = "", name = "", width = "0", height = "0"] = match;
    if (id === frame.id || !hasCode(context.collected.designContexts.get(id) ?? [])) continue;
    if (parts.some((part) => part.id === id)) continue;
    parts.push({
      ...frame,
      id,
      name: `${frame.name} · ${name} ${parts.length + 1}`,
      dir: `${frame.dir}/${parts.length + 1}-${slugify(name)}`,
      width: Number(width),
      height: Number(height),
    });
  }
  return parts;
}

async function materializeFrame(context: Context, frame: FrameEntry): Promise<MaterializedFrame> {
  const dir = path.join(context.out, frame.dir);
  await mkdir(dir, { recursive: true });
  const texts = context.collected.designContexts.get(frame.id) ?? [];
  const screenshot = context.collected.screenshots.get(frame.id);
  if (screenshot) await fetchInto(context, screenshot, path.join(dir, "figma.png"));

  const code = hasCode(texts);
  const written = code ? await writeReference(context, frame, texts) : undefined;
  const partEntries = code || texts.length === 0 ? [] : sparseParts(context, frame, texts);
  const parts: MaterializedFrame[] = [];
  for (const part of partEntries) parts.push(await materializeFrame(context, part));

  const files = {
    reference: code,
    clone: code,
    screenshot: existsSync(path.join(dir, "figma.png")),
  };
  await writeFile(
    path.join(dir, "notes.md"),
    frameNotes({
      fileKey: context.fileKey,
      frame,
      files,
      annotations: annotationsFor(context, frame.id),
      styles: written?.styles ?? [],
      components: written?.components ?? [],
      missingAssets: written?.unresolved ?? [],
      parts: partEntries,
    }),
  );
  return { ...frame, files, ...(parts.length > 0 ? { parts } : {}) };
}

async function writeIcons(out: string, collected: Collected): Promise<void> {
  if (collected.icons.size === 0) return;
  await mkdir(path.join(out, "icons"), { recursive: true });
  const rows: string[] = [];
  for (const [id, icon] of [...collected.icons].sort((a, b) =>
    a[1].name.localeCompare(b[1].name, "ru"),
  )) {
    const file = `${slugify(icon.name)}.svg`;
    await writeFile(path.join(out, "icons", file), icon.svg);
    rows.push(`| ![](${file}) | ${icon.name} | \`${file}\` | ${id} |`);
  }
  const table = `| | Компонент | Файл | Узел |\n|---|---|---|---|\n${rows.join("\n")}`;
  await writeFile(
    path.join(out, "icons", "README.md"),
    `# Иконки, значки сигналов и знак\n\nSVG компонентов со страницы «Компоненты». Цвет штриха — как в макете; в коде задаём его через \`currentColor\`.\n\n${table}\n`,
  );
}

async function writeTokens(out: string, collected: Collected): Promise<void> {
  if (!collected.variables) return;
  await mkdir(path.join(out, "tokens"), { recursive: true });
  await writeFile(
    path.join(out, "tokens", "variables.json"),
    `${JSON.stringify(collected.variables, null, 2)}\n`,
  );
  await writeFile(path.join(out, "tokens", "tokens.css"), tokensCss(collected.variables));
}

/** Пишет файлы всех кадров плана, иконки, токены и `components.md`. */
export async function materialize(options: MaterializeOptions): Promise<Materialized> {
  const listingNames = new Map<string, string>();
  for (const listing of options.collected.listings) {
    for (const node of listing.nodes) listingNames.set(node.id, node.name);
  }
  const context: Context = {
    ...options,
    components: new Map(),
    missing: [],
    problems: [],
    listingNames,
  };
  const frames: MaterializedFrame[] = [];
  for (const frame of options.frames) frames.push(await materializeFrame(context, frame));

  await writeIcons(options.out, options.collected);
  await writeTokens(options.out, options.collected);
  await writeFile(
    path.join(options.out, "components.md"),
    componentsMarkdown(options.fileKey, [...context.components.values()]),
  );
  const pageNotes = new Map(
    options.notes.map((page) => [
      page.page,
      page.pageLevel.map((id) => annotationNote(context, id)),
    ]),
  );
  return { frames, missing: context.missing, pageNotes, problems: context.problems };
}
