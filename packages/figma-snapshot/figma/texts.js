// Скрипт для use_figma (Plugin API). Текст аннотаций по id в порядке слоёв.
// Перед вызовом замени __PAGE_ID__ и __IDS__ (JSON-массив id). Ответ — не больше 48 КБ,
// поэтому длинные страницы выгружаются несколькими вызовами.
const page = await figma.getNodeByIdAsync("__PAGE_ID__");
await figma.setCurrentPageAsync(page);

const annotations = [];
const absent = [];
for (const id of __IDS__) {
  const node = await figma.getNodeByIdAsync(id);
  if (!node || !("findAllWithCriteria" in node)) {
    absent.push(id);
    continue;
  }
  const texts = node.findAllWithCriteria({ types: ["TEXT"] });
  annotations.push({
    id,
    texts: texts
      .filter((text) => text.visible && text.characters.trim())
      .map((text) => ({
        text: text.characters,
        bold: typeof text.fontWeight === "number" && text.fontWeight >= 600,
      })),
  });
}
return { kind: "figma-snapshot/texts", absent, annotations };
