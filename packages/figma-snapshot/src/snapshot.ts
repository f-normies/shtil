/**
 * Снимок макетов Figma для работы без Figma MCP. Порядок обновления — README пакета.
 *
 *   node src/snapshot.ts --sessions <журнал или каталог сессии> [--sessions …] [--skip-compare]
 *
 * 1. Собирает из журналов ответы Figma MCP (collect.ts); новый ответ на тот же узел побеждает.
 * 2. Пишет код, ресурсы, скриншоты и заметки кадров (materialize.ts), удаляет лишние каталоги.
 * 3. Собирает клоны index.html (build.ts) и сверяет их с эталоном (compare.ts).
 * 4. Пишет frames.json, README страниц, галерею и отчёт о пропусках `.cache/missing.json`.
 */
import { existsSync } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { parseArgs } from "node:util";

import { buildClone, cornerColor, type VariantLayout, writeFonts } from "./build.ts";
import { type Collected, collect, findTranscripts } from "./collect.ts";
import { compareClones } from "./compare.ts";
import { pageReadme } from "./docs.ts";
import { galleryPage } from "./gallery.ts";
import { planSnapshot } from "./manifest.ts";
import { type MaterializedFrame, materialize } from "./materialize.ts";
import { markIncomplete, missingReport, pruneOrphans } from "./report.ts";
import { parseVariants, variantProps } from "./variants.ts";

const FILE_KEY = "Izz3l5Cmyyd82Ts1znaLaQ";
const PACKAGE = path.resolve(import.meta.dirname, "..");
const OUT = path.resolve(PACKAGE, "../../docs/design/figma");
const CACHE = path.join(PACKAGE, ".cache");
const LEDGER = path.join(CACHE, "downloads.json");
/** Ниже этого сходства клон помечается неполным в notes.md. */
const INCOMPLETE_BELOW = 97;
const PAGES = [
  { page: "Врач", dir: "doctor", title: "Кабинет врача" },
  { page: "Пациент", dir: "patient", title: "Кабинет пациента" },
  { page: "Компоненты", dir: "components", title: "Компоненты и доски" },
];

type Row = MaterializedFrame & { match?: number };

const log = (line: string) => process.stdout.write(`${line}\n`);
const byName = (a: { name: string }, b: { name: string }) =>
  a.name.localeCompare(b.name, "ru", { numeric: true });

function flatten(frames: readonly MaterializedFrame[]): MaterializedFrame[] {
  return frames.flatMap((frame) => [frame, ...flatten(frame.parts ?? [])]);
}

/** Раскладка вариантов для набора компонентов; для остальных кадров — undefined. */
async function variantLayout(
  collected: Collected,
  frame: MaterializedFrame,
): Promise<VariantLayout | undefined> {
  if (frame.type !== "COMPONENT_SET" || !frame.files.screenshot) return undefined;
  const xml = collected.metadata.findLast((item) => item.includes(`id="${frame.id}"`));
  const variants = xml ? parseVariants(xml, frame.id) : [];
  if (variants.length === 0) return undefined;
  const dir = path.join(OUT, frame.dir);
  const code = await readFile(path.join(dir, "reference.tsx"), "utf8");
  return {
    background: await cornerColor(path.join(dir, "figma.png")),
    items: variants.map((variant) => ({
      left: variant.x,
      top: variant.y,
      width: variant.width,
      height: variant.height,
      props: variantProps(code, variant.properties),
    })),
  };
}

async function readJson<T>(file: string, fallback: T): Promise<T> {
  return existsSync(file) ? (JSON.parse(await readFile(file, "utf8")) as T) : fallback;
}

/** Журнал скачиваний хранит пути относительно снимка: worktree и основная копия совпадают. */
async function loadLedger(): Promise<Map<string, string>> {
  const saved = await readJson<Record<string, string>>(LEDGER, {});
  return new Map(Object.entries(saved).map(([file, url]) => [path.join(OUT, file), url]));
}

async function saveLedger(ledger: Map<string, string>): Promise<void> {
  const relative = [...ledger].map(([file, url]) => [path.relative(OUT, file), url]);
  await writeFile(LEDGER, `${JSON.stringify(Object.fromEntries(relative), null, 2)}\n`);
}

async function previousMatches(): Promise<Map<string, number>> {
  const previous = await readJson<{ frames: { id: string; match?: number }[] }>(
    path.join(OUT, "frames.json"),
    { frames: [] },
  );
  return new Map(
    previous.frames.flatMap((frame) =>
      frame.match === undefined ? [] : [[frame.id, frame.match]],
    ),
  );
}

/** Собирает клоны; ошибка одного кадра не обрывает снимок, кадр остаётся без клона. */
async function buildClones(collected: Collected, frames: MaterializedFrame[]): Promise<Row[]> {
  await writeFonts(OUT);
  const rows: Row[] = [];
  for (const frame of frames) {
    if (!frame.files.reference) {
      rows.push(frame);
      continue;
    }
    try {
      await buildClone(OUT, frame, await variantLayout(collected, frame));
      rows.push(frame);
    } catch (error) {
      log(`! клон не собран: ${frame.dir}: ${String(error)}`);
      rows.push({ ...frame, files: { ...frame.files, clone: false } });
    }
  }
  return rows;
}

async function writeReports(rows: readonly Row[], pageNotes: Map<string, string[]>): Promise<void> {
  const generated = new Date().toLocaleDateString("sv-SE");
  const manifest = rows.map(({ parts: _parts, ...frame }) => frame);
  await writeFile(
    path.join(OUT, "frames.json"),
    `${JSON.stringify({ fileKey: FILE_KEY, generated, frames: manifest }, null, 2)}\n`,
  );
  for (const { page, dir, title } of PAGES) {
    const pageRows = rows.filter((row) => row.page === page).sort(byName);
    const table = pageRows.map((row) => ({ frame: row, files: row.files, match: row.match }));
    await writeFile(
      path.join(OUT, dir, "README.md"),
      pageReadme(title, dir, table, pageNotes.get(page) ?? []),
    );
  }
  const sections = PAGES.map(({ page, title }) => ({
    title,
    items: rows
      .filter((row) => row.page === page)
      .sort(byName)
      .map((row) => ({ ...row, clone: row.files.clone, screenshot: row.files.screenshot })),
  }));
  await writeFile(path.join(OUT, "index.html"), galleryPage(generated, sections));
  for (const row of rows) {
    if (row.match === undefined || row.match >= INCOMPLETE_BELOW) continue;
    const notes = path.join(OUT, row.dir, "notes.md");
    await writeFile(notes, markIncomplete(await readFile(notes, "utf8"), row.match));
    log(`Сходство ниже ${INCOMPLETE_BELOW} %: ${row.match.toFixed(1)} % — ${row.dir}`);
  }
}

async function main(): Promise<void> {
  const { values } = parseArgs({
    options: { sessions: { type: "string", multiple: true }, "skip-compare": { type: "boolean" } },
  });
  if (!values.sessions?.length) throw new Error("Укажи --sessions <журнал или каталог сессии>");
  const transcripts = (await Promise.all(values.sessions.map(findTranscripts))).flat();
  log(`Журналов: ${transcripts.length}`);

  const collected = await collect(transcripts);
  const plan = planSnapshot(collected.listings);
  await mkdir(CACHE, { recursive: true });
  const ledger = await loadLedger();
  const result = await materialize({ out: OUT, fileKey: FILE_KEY, collected, ledger, ...plan });
  await saveLedger(ledger);
  const all = flatten(result.frames);
  const removed = await pruneOrphans(
    OUT,
    PAGES.map((page) => page.dir),
    all.map((frame) => frame.dir),
  );
  log(`Кадров: ${all.length}, с кодом: ${all.filter((frame) => frame.files.reference).length}`);
  for (const dir of removed) log(`Удалён каталог кадра, которого нет в макете: ${dir}`);
  for (const problem of result.problems) log(`! ${problem}`);

  const built = await buildClones(collected, all);
  const matches = await previousMatches();
  if (!values["skip-compare"]) {
    const comparable = built.filter((frame) => frame.files.clone && frame.files.screenshot);
    for (const comparison of await compareClones(OUT, comparable)) {
      if (comparison.match === undefined) {
        matches.delete(comparison.id);
        log(`! ${comparison.id}: ${comparison.note}`);
      } else matches.set(comparison.id, comparison.match);
    }
  }
  const rows = built.map((frame) => {
    const match = matches.get(frame.id);
    return match === undefined ? frame : { ...frame, match };
  });
  await writeReports(rows, result.pageNotes);

  const missing = missingReport(all, plan.notes, new Set(collected.annotationTexts.keys()));
  const missingAssets = [...new Set(result.missing.map((asset) => asset.nodeId))];
  await writeFile(path.join(CACHE, "missing.json"), `${JSON.stringify(missing, null, 2)}\n`);
  await writeFile(
    path.join(CACHE, "missing-assets.json"),
    `${JSON.stringify(missingAssets, null, 2)}\n`,
  );
  const annotations = Object.values(missing.annotationsWithoutText).flat().length;
  log(
    `Пропуски: без кода ${missing.framesWithoutCode.length}, без скриншота ${missing.framesWithoutScreenshot.length}, аннотаций без текста ${annotations} (.cache/missing.json)`,
  );
  log(
    `Ресурсов без SVG: ${missingAssets.length} (.cache/missing-assets.json, выгрузка — figma/svgs.js)`,
  );
}

await main();
