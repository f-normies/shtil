import assert from "node:assert/strict";
import test from "node:test";

import { annotationMarkdown, parseComponents, parseTextStyles } from "./notes.ts";

test("описания компонентов разбираются по заголовкам с id узла", () => {
  const block = [
    "Component descriptions: The following components have usage descriptions.",
    "",
    "## Врач v4/Меню",
    "**Node ID:** 118:140",
    "",
    "Меню окна, 232×832.",
    "Пункты — экземпляры «Пункт меню».",
    "",
    "## Знак/Врач",
    "**Node ID:** 21:28",
    "",
    "Знак «Штиль» для кабинета врача.",
  ].join("\n");

  assert.deepEqual(parseComponents(block), [
    {
      name: "Врач v4/Меню",
      nodeId: "118:140",
      description: "Меню окна, 232×832.\nПункты — экземпляры «Пункт меню».",
    },
    { name: "Знак/Врач", nodeId: "21:28", description: "Знак «Штиль» для кабинета врача." },
  ]);
});

test("текстовые стили и эффекты разбираются в список", () => {
  const block =
    'These styles are contained in the design: Врач v4/Заголовок: Font(family: "Onest", style: SemiBold, size: 20, weight: 600, lineHeight: 26, letterSpacing: 0), Врач/Тень карточки: Effect(type: DROP_SHADOW, color: #101A200F, offset: (0, 1), radius: 2, spread: 0).';

  assert.deepEqual(parseTextStyles(block), [
    {
      name: "Врач v4/Заголовок",
      value:
        'Font(family: "Onest", style: SemiBold, size: 20, weight: 600, lineHeight: 26, letterSpacing: 0)',
    },
    {
      name: "Врач/Тень карточки",
      value: "Effect(type: DROP_SHADOW, color: #101A200F, offset: (0, 1), radius: 2, spread: 0)",
    },
  ]);
});

test("аннотация становится Markdown: жирные строки — подзаголовки, тире — список", () => {
  const markdown = annotationMarkdown([
    { text: "Дневник. Прокрутка", bold: true },
    { text: "— Экран прокручивается.", bold: false },
    { text: "— Фильтры: «Все».", bold: false },
    { text: "Движение", bold: false },
    { text: "Пружина 260/28/1.", bold: false },
  ]);

  assert.equal(
    markdown,
    [
      "**Дневник. Прокрутка**",
      "",
      "- Экран прокручивается.",
      "- Фильтры: «Все».",
      "",
      "Движение",
      "",
      "Пружина 260/28/1.",
    ].join("\n"),
  );
});
