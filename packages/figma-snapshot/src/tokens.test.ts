import assert from "node:assert/strict";
import test from "node:test";

import { tokensCss } from "./tokens.ts";

const collections = [
  {
    name: "Палитра",
    modes: ["Значение"],
    variables: [
      { name: "neutral/0", type: "COLOR", web: "var(--palette-neutral-0)", values: ["#ffffff"] },
      { name: "sand/0", type: "COLOR", web: "var(--palette-sand-0)", values: ["#fffdf9"] },
    ],
  },
  {
    name: "Отступы",
    modes: ["Значение"],
    variables: [{ name: "space/4", type: "FLOAT", web: "calc(var(--spacing) * 4)", values: [16] }],
  },
  {
    name: "Форма",
    modes: ["Врач", "Пациент"],
    variables: [{ name: "radius/md", type: "FLOAT", web: "var(--radius-md)", values: [8, 16] }],
  },
  {
    name: "Цвет",
    modes: ["Врач", "Пациент · тёплая"],
    variables: [
      {
        name: "color/surface",
        type: "COLOR",
        web: "var(--color-surface)",
        values: [{ alias: "Палитра/neutral/0" }, { alias: "Палитра/sand/0" }],
      },
      {
        name: "color/scrim",
        type: "COLOR",
        web: "var(--color-scrim)",
        values: ["#151d2333", "#0c283433"],
      },
    ],
  },
];

test("первый режим коллекции попадает в :root, остальные — в селектор режима", () => {
  const css = tokensCss(collections);

  assert.match(css, /:root \{[^}]*--palette-neutral-0: #ffffff;/);
  assert.match(css, /:root \{[^}]*--radius-md: 8px;/);
  assert.match(css, /:root \{[^}]*--color-surface: var\(--palette-neutral-0\);/);
  assert.match(css, /\[data-forma="patsient"\] \{\n {2}--radius-md: 16px;\n\}/);
  assert.match(
    css,
    /\[data-tsvet="patsient-teplaya"\] \{\n {2}--color-surface: var\(--palette-sand-0\);\n {2}--color-scrim: #0c283433;\n\}/,
  );
});

test("переменная без имени CSS-переменной остаётся комментарием", () => {
  const css = tokensCss(collections);

  assert.match(css, /\/\* space\/4: 16 → calc\(var\(--spacing\) \* 4\) \*\//);
});
