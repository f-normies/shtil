/**
 * Попиксельная сверка клона с эталоном: Chromium рисует `index.html` в масштабе 1:1,
 * pixelmatch сравнивает со скриншотом Figma. Сходство — доля совпавших пикселей.
 * Разница и снимок клона пишутся в `.cache/diff/` (не в git).
 */
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

import pixelmatch from "pixelmatch";
import { chromium } from "playwright";
import { PNG } from "pngjs";

import type { FrameEntry } from "./manifest.ts";

const PACKAGE = path.resolve(import.meta.dirname, "..");
export const DIFF_DIR = path.join(PACKAGE, ".cache", "diff");
/** Порог pixelmatch: разницу сглаживания текста между Figma и Chromium не считаем. */
const THRESHOLD = 0.1;

export interface Comparison {
  readonly id: string;
  /** Сходство в процентах; `undefined`, если размеры не совпали. */
  readonly match?: number;
  readonly note?: string;
}

/** Рисует клоны в Chromium 1:1 и сравнивает с `figma.png`; разницу пишет в `.cache/diff/`. */
export async function compareClones(
  out: string,
  frames: readonly FrameEntry[],
): Promise<Comparison[]> {
  await mkdir(DIFF_DIR, { recursive: true });
  const browser = await chromium.launch();
  const context = await browser.newContext({ deviceScaleFactor: 1 });
  const page = await context.newPage();
  const results: Comparison[] = [];
  try {
    for (const frame of frames) {
      const dir = path.join(out, frame.dir);
      await page.setViewportSize({ width: frame.width, height: frame.height });
      await page.goto(pathToFileURL(path.join(dir, "index.html")).href);
      await page.evaluate(() => document.fonts.ready);
      const actual = PNG.sync.read(await page.locator("#frame").screenshot());
      const expected = PNG.sync.read(await readFile(path.join(dir, "figma.png")));
      const key = frame.dir.replaceAll("/", "__");
      await writeFile(path.join(DIFF_DIR, `${key}.clone.png`), PNG.sync.write(actual));
      if (actual.width !== expected.width || actual.height !== expected.height) {
        const note = `размер клона ${actual.width}×${actual.height}, эталона ${expected.width}×${expected.height}`;
        results.push({ id: frame.id, note });
        continue;
      }
      const diff = new PNG({ width: actual.width, height: actual.height });
      const mismatched = pixelmatch(
        expected.data,
        actual.data,
        diff.data,
        actual.width,
        actual.height,
        {
          threshold: THRESHOLD,
        },
      );
      await writeFile(path.join(DIFF_DIR, `${key}.diff.png`), PNG.sync.write(diff));
      results.push({
        id: frame.id,
        match: 100 * (1 - mismatched / (actual.width * actual.height)),
      });
    }
  } finally {
    await browser.close();
  }
  return results;
}
