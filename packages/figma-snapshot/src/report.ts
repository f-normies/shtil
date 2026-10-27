/**
 * Уборка и отчёт после снимка: удалить каталоги кадров, которых больше нет в макете,
 * назвать, чего не хватает для полного снимка, пометить неполные клоны.
 */
import { readdir, rm } from "node:fs/promises";
import path from "node:path";

import type { PageNotes } from "./manifest.ts";

export interface ReportFrame {
  readonly id: string;
  readonly name: string;
  readonly files: {
    readonly reference: boolean;
    readonly clone: boolean;
    readonly screenshot: boolean;
  };
}

export interface MissingReport {
  readonly framesWithoutCode: string[];
  readonly framesWithoutScreenshot: string[];
  /** id аннотаций без текста по страницам: их выгружает `figma/texts.js`. */
  readonly annotationsWithoutText: Record<string, string[]>;
}

async function subdirectories(dir: string): Promise<string[]> {
  try {
    const entries = await readdir(dir, { withFileTypes: true });
    return entries.filter((entry) => entry.isDirectory()).map((entry) => entry.name);
  } catch {
    return [];
  }
}

/**
 * Удаляет каталоги кадров, которых нет в плане: кадр переименовали или удалили в Figma.
 * Внутри кадра удаляет и части, которых больше нет; `assets/` не трогает.
 * Возвращает удалённые пути относительно `out`.
 */
export async function pruneOrphans(
  out: string,
  pageDirs: readonly string[],
  keepDirs: readonly string[],
): Promise<string[]> {
  const keep = new Set(keepDirs);
  const removed: string[] = [];
  const remove = async (relative: string) => {
    await rm(path.join(out, relative), { recursive: true, force: true });
    removed.push(relative);
  };
  for (const page of pageDirs) {
    for (const name of await subdirectories(path.join(out, page))) {
      const frame = `${page}/${name}`;
      if (!keep.has(frame)) {
        await remove(frame);
        continue;
      }
      for (const part of await subdirectories(path.join(out, frame))) {
        if (part !== "assets" && !keep.has(`${frame}/${part}`)) await remove(`${frame}/${part}`);
      }
    }
  }
  return removed;
}

/** Чего не хватает для полного снимка: кадры без кода или скриншота, аннотации без текста. */
export function missingReport(
  frames: readonly ReportFrame[],
  notes: readonly PageNotes[],
  annotationTexts: ReadonlySet<string>,
): MissingReport {
  const label = (frame: ReportFrame) => `${frame.id} ${frame.name}`;
  const annotationsWithoutText: Record<string, string[]> = {};
  for (const page of notes) {
    const ids = [...Object.values(page.byFrame).flat(), ...page.pageLevel];
    const missing = ids.filter((id) => !annotationTexts.has(id));
    if (missing.length > 0) annotationsWithoutText[page.page] = missing;
  }
  return {
    framesWithoutCode: frames.filter((frame) => !frame.files.reference).map(label),
    framesWithoutScreenshot: frames.filter((frame) => !frame.files.screenshot).map(label),
    annotationsWithoutText,
  };
}

/** Ставит в заметку кадра предупреждение о неполном клоне сразу под заголовком. */
export function markIncomplete(notes: string, match: number): string {
  const percent = match.toFixed(1).replace(".", ",");
  const warning = `> **Клон неполный: сходство с эталоном ${percent} %.** Ориентир — \`figma.png\`. MCP вернул код не для всех элементов, \`reference.tsx\` может их не содержать.`;
  const afterGenerated = notes.indexOf("\n\n", notes.indexOf("\n\n") + 2);
  if (afterGenerated === -1) return `${notes}\n${warning}\n`;
  return `${notes.slice(0, afterGenerated + 2)}${warning}\n\n${notes.slice(afterGenerated + 2)}`;
}
