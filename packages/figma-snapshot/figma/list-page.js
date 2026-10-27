// Скрипт для use_figma (Plugin API). Перечисляет кадры страницы и разделы досок.
// Перед вызовом замени __PAGE_ID__ на id страницы. Результат забирает materialize.ts.
// Текст аннотаций выгружает texts.js: use_figma возвращает не больше 48 КБ.
const page = await figma.getNodeByIdAsync("__PAGE_ID__");
await figma.setCurrentPageAsync(page);

const BOARDS = new Set(["Врач v4", "Пациент", "Компоненты", "Раздел · Уровни шкалы"]);

// Раздел «Уровни шкалы» слишком велик для одного кадра: берём его части из «Ряда».
function childrenOf(node) {
  const children = node.children.filter((child) => child.type !== "TEXT");
  if (node.name !== "Раздел · Уровни шкалы") return children;
  return children.flatMap((child) => (child.name === "Ряд" && "children" in child ? child.children : [child]));
}

function describe(node, depth) {
  const box = node.absoluteBoundingBox ?? { x: node.x, y: node.y, width: node.width, height: node.height };
  const item = {
    id: node.id,
    name: node.name,
    type: node.type,
    x: Math.round(box.x),
    y: Math.round(box.y),
    width: Math.round(box.width),
    height: Math.round(box.height),
  };
  if (depth < 3 && BOARDS.has(node.name) && "children" in node) {
    item.children = childrenOf(node).map((child) => describe(child, depth + 1));
  }
  // Разделы досок: заголовок и пояснение раздела плюс один «Ряд» с компонентами.
  if (depth > 0 && node.name.startsWith("Раздел ·") && "children" in node) {
    item.caption = node.children
      .filter((child) => child.type === "TEXT")
      .map((child) => child.characters)
      .join("\n");
  }
  return item;
}

return {
  kind: "figma-snapshot/frames",
  page: { id: page.id, name: page.name },
  nodes: page.children.filter((node) => node.type !== "TEXT" || node.name.startsWith("Подпись")).map((node) => describe(node, 0)),
};
