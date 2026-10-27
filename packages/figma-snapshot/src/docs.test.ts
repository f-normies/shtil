import assert from "node:assert/strict";
import test from "node:test";

import { frameNotes } from "./docs.ts";

test("заметка кадра ссылается на узел Figma, перечисляет файлы, аннотации, стили и компоненты", () => {
  const markdown = frameNotes({
    fileKey: "Izz3l5Cmyyd82Ts1znaLaQ",
    frame: {
      id: "121:63",
      name: "Врач v4 · 02 Пациенты",
      page: "Врач",
      kind: "screen",
      dir: "doctor/02-patsienty",
      width: 1280,
      height: 832,
    },
    files: { reference: true, clone: true, screenshot: true },
    annotations: ["**Пациенты**\n\nСписок слева."],
    styles: [{ name: "Врач v4/Заголовок", value: "Font(size: 20)" }],
    components: [
      { name: "Врач v4/Меню", nodeId: "118:140", description: "Меню окна, 232×832. Пункты меню." },
    ],
    missingAssets: [],
  });

  assert.match(markdown, /^# Врач v4 · 02 Пациенты\n/);
  assert.match(
    markdown,
    /https:\/\/www\.figma\.com\/design\/Izz3l5Cmyyd82Ts1znaLaQ\/\?node-id=121-63/,
  );
  assert.match(markdown, /1280 × 832/);
  assert.match(markdown, /\[`index\.html`\]\(index\.html\)/);
  assert.match(markdown, /## Аннотации из макета\n\n\*\*Пациенты\*\*\n\nСписок слева\./);
  assert.match(markdown, /- `Врач v4\/Заголовок` — Font\(size: 20\)/);
  assert.match(
    markdown,
    /- \[Врач v4\/Меню\]\(\.\.\/\.\.\/components\.md#118-140\) — Меню окна, 232×832\./,
  );
});
