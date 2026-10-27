// Скрипт для use_figma (Plugin API). SVG ресурсов, которых MCP не вернул в коде кадра.
// Перед вызовом замени __IDS__ (JSON-массив id из .cache/missing-assets.json).
// Ответ — не больше 48 КБ: длинный список выгружай несколькими вызовами.
//
// Слои внутри экземпляров («I<экземпляр>;<слой>») плагину не видны: у экземпляра пустой
// children. Поэтому ресурс ищем по шагам:
// 1. узел доступен напрямую — экспорт узла;
// 2. слой подменён свойством INSTANCE_SWAP — экспорт подставленного компонента, а цвет
//    ink (#151D23) заменяется цветом того же слоя в главном компоненте;
// 3. иначе — экспорт слоя из определения компонента (последний сегмент id).
const page = await figma.getNodeByIdAsync("109:15");
await figma.setCurrentPageAsync(page);

const INK = "#151D23";

function colorsOf(svg) {
  return [...svg.matchAll(/(?:stroke|fill)="(#[0-9A-Fa-f]{6})"/g)].map((match) => match[1].toUpperCase());
}

async function swapped(id) {
  const [rootId, layerId] = id.slice(1).split(";");
  const root = await figma.getNodeByIdAsync(rootId);
  const layer = await figma.getNodeByIdAsync(layerId);
  const property = layer?.componentPropertyReferences?.mainComponent;
  if (!root || root.type !== "INSTANCE" || !property) return undefined;
  const value = root.componentProperties[property]?.value;
  const component = value ? await figma.getNodeByIdAsync(String(value)) : undefined;
  if (!component) return undefined;
  let svg = await component.exportAsync({ format: "SVG_STRING" });
  const tint = colorsOf(await layer.exportAsync({ format: "SVG_STRING" })).find((color) => color !== "#FFFFFF");
  if (tint && tint !== INK) svg = svg.replaceAll(`"${INK}"`, `"${tint}"`);
  return { name: component.name, svg, via: "swap" };
}

const svgs = {};
const absent = [];
async function resolve(id) {
  const direct = await figma.getNodeByIdAsync(id);
  if (direct && "exportAsync" in direct) {
    return { name: direct.name, svg: await direct.exportAsync({ format: "SVG_STRING" }), via: "node" };
  }
  if (!id.startsWith("I")) return undefined;
  const viaSwap = await swapped(id);
  if (viaSwap) return viaSwap;
  const definition = await figma.getNodeByIdAsync(id.split(";").pop());
  if (!definition || !("exportAsync" in definition)) return undefined;
  return { name: definition.name, svg: await definition.exportAsync({ format: "SVG_STRING" }), via: "definition" };
}
for (const id of __IDS__) {
  try {
    const found = await resolve(id);
    if (found) svgs[id] = found;
    else absent.push({ id, reason: "узел не найден" });
  } catch (error) {
    // Пустой слой без видимых частей Figma не экспортирует.
    absent.push({ id, reason: String(error.message ?? error) });
  }
}
return { kind: "figma-snapshot/svgs", absent, svgs };
