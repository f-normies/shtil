/**
 * Все ответы Figma MCP из журналов сессий, собранные по узлам. Ответы use_figma узнаются
 * по полю `kind`, которое возвращают скрипты из `figma/`.
 */
import { readdir, stat } from "node:fs/promises";
import path from "node:path";

import type { Listing } from "./manifest.ts";
import type { AnnotationText } from "./notes.ts";
import type { FigmaCollection } from "./tokens.ts";
import { collectToolResults } from "./transcript.ts";

const MCP = "mcp__plugin_figma_figma__";
// Скачиваем только временные адреса Figma MCP: адрес из журнала не должен уводить на чужой хост.
const SCREENSHOT_URL = /"image_url":"(https:\/\/www\.figma\.com\/api\/mcp\/asset\/[^"]+)"/;

/** id из адреса Figma (`121-63`) и из ответов MCP (`121:63`) — к одному виду. */
const nodeIdOf = (value: unknown) => String(value).replace(/^(\d+)-(\d+)$/, "$1:$2");

export interface Svg {
  readonly name: string;
  readonly svg: string;
}

export interface Collected {
  readonly listings: Listing[];
  readonly annotationTexts: Map<string, AnnotationText[]>;
  /** Тексты ответа get_design_context: первый — код. Ответ с кодом важнее разреженного. */
  readonly designContexts: Map<string, string[]>;
  readonly screenshots: Map<string, string>;
  /** SVG узлов, которых MCP не вернул в коде (`figma/svgs.js`). */
  readonly svgs: Map<string, Svg>;
  /** Иконки, значки сигналов и знак (`figma/icons.js`). */
  readonly icons: Map<string, Svg>;
  /** XML ответов get_metadata: позиции вариантов в наборах компонентов. */
  readonly metadata: string[];
  variables?: FigmaCollection[];
}

export const hasCode = (texts: readonly string[]) => texts[0]?.includes("export default") ?? false;

/** Все `*.jsonl` в каталоге сессии, включая журналы субагентов. Файл журнала — сам по себе. */
export async function findTranscripts(directory: string): Promise<string[]> {
  if ((await stat(directory)).isFile()) return [directory];
  const entries = await readdir(directory, { recursive: true, withFileTypes: true });
  return entries
    .filter((item) => item.isFile() && item.name.endsWith(".jsonl"))
    .map((item) => path.join(item.parentPath, item.name))
    .sort();
}

function parseJson(text: string | undefined): Record<string, unknown> | undefined {
  try {
    const value: unknown = JSON.parse(text ?? "");
    return value && typeof value === "object" ? (value as Record<string, unknown>) : undefined;
  } catch {
    return undefined;
  }
}

/** Собирает ответы Figma MCP из журналов: код, скриншоты, перечни, тексты, SVG, переменные. */
export async function collect(transcripts: readonly string[]): Promise<Collected> {
  const collected: Collected = {
    listings: [],
    annotationTexts: new Map(),
    designContexts: new Map(),
    screenshots: new Map(),
    svgs: new Map(),
    icons: new Map(),
    metadata: [],
  };
  const listings = new Map<string, Listing>();

  for (const { input, texts } of await collectToolResults(
    transcripts,
    `${MCP}get_design_context`,
  )) {
    const nodeId = nodeIdOf(input.nodeId);
    const previous = collected.designContexts.get(nodeId);
    if (!previous || hasCode(texts) || !hasCode(previous))
      collected.designContexts.set(nodeId, texts);
  }
  for (const { input, texts } of await collectToolResults(transcripts, `${MCP}get_screenshot`)) {
    const url = texts.join("\n").match(SCREENSHOT_URL)?.[1];
    if (url) collected.screenshots.set(nodeIdOf(input.nodeId), url);
  }
  for (const { texts } of await collectToolResults(transcripts, `${MCP}get_metadata`)) {
    collected.metadata.push(texts.join("\n"));
  }
  for (const { texts } of await collectToolResults(transcripts, `${MCP}use_figma`)) {
    const value = parseJson(texts[0]);
    switch (value?.kind) {
      case "figma-snapshot/frames": {
        const listing = value as unknown as Listing;
        listings.set(listing.page.name, listing);
        for (const node of listing.nodes) {
          if (node.texts) collected.annotationTexts.set(node.id, [...node.texts]);
        }
        break;
      }
      case "figma-snapshot/texts":
        for (const item of value.annotations as { id: string; texts: AnnotationText[] }[]) {
          collected.annotationTexts.set(item.id, item.texts);
        }
        break;
      case "figma-snapshot/svgs":
        for (const [id, svg] of Object.entries(value.svgs as Record<string, Svg>))
          collected.svgs.set(id, svg);
        break;
      case "figma-snapshot/icons":
        for (const [id, svg] of Object.entries(value.svgs as Record<string, Svg>))
          collected.icons.set(id, svg);
        break;
      case "figma-snapshot/variables":
        collected.variables = value.collections as FigmaCollection[];
        break;
    }
  }
  collected.listings.push(...listings.values());
  return collected;
}
