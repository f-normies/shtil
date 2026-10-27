import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { markIncomplete, missingReport, pruneOrphans } from "./report.ts";

test("каталоги кадров, которых нет в плане, удаляются; README страницы и части кадра остаются", async () => {
  const out = await mkdtemp(path.join(os.tmpdir(), "figma-snapshot-"));
  for (const dir of [
    "doctor/02-patsienty",
    "doctor/99-udalen",
    "doctor/board-x/1-sektsiya",
    "doctor/board-x/2-staraya",
  ]) {
    await mkdir(path.join(out, dir), { recursive: true });
  }
  await writeFile(path.join(out, "doctor", "README.md"), "таблица");

  const removed = await pruneOrphans(
    out,
    ["doctor"],
    ["doctor/02-patsienty", "doctor/board-x", "doctor/board-x/1-sektsiya"],
  );

  assert.deepEqual(removed.sort(), ["doctor/99-udalen", "doctor/board-x/2-staraya"]);
  assert.equal(existsSync(path.join(out, "doctor", "README.md")), true);
  assert.equal(existsSync(path.join(out, "doctor", "board-x", "1-sektsiya")), true);
});

test("отчёт называет кадры без кода и скриншота и аннотации без текста", () => {
  const report = missingReport(
    [
      { id: "1:1", name: "A", files: { reference: true, clone: true, screenshot: true } },
      { id: "1:2", name: "B", files: { reference: false, clone: false, screenshot: true } },
      { id: "1:3", name: "C", files: { reference: true, clone: true, screenshot: false } },
    ],
    [{ page: "Врач", byFrame: { "1:1": ["a:1", "a:2"] }, pageLevel: ["g:1"] }],
    new Set(["a:1"]),
  );

  assert.deepEqual(report, {
    framesWithoutCode: ["1:2 B"],
    framesWithoutScreenshot: ["1:3 C"],
    annotationsWithoutText: { Врач: ["a:2", "g:1"] },
  });
});

test("неполный клон помечается в заметке сразу под заголовком", () => {
  const notes = "# Кадр\n\nСгенерировано.\n\n## Файлы\n";

  assert.equal(
    markIncomplete(notes, 89.24),
    "# Кадр\n\nСгенерировано.\n\n> **Клон неполный: сходство с эталоном 89,2 %.** Ориентир — `figma.png`. MCP вернул код не для всех элементов, `reference.tsx` может их не содержать.\n\n## Файлы\n",
  );
});
