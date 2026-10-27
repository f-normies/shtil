// Скрипт для use_figma (Plugin API). SVG иконок, значков сигналов и знака со страницы «Компоненты».
const page = await figma.getNodeByIdAsync("17:2");
await figma.setCurrentPageAsync(page);
const components = page.findAllWithCriteria({ types: ["COMPONENT"] })
  .filter((node) => /^(Иконка|Сигнал|Знак)\//.test(node.name));
const svgs = {};
for (const node of components) {
  svgs[node.id] = { name: node.name, svg: await node.exportAsync({ format: "SVG_STRING" }) };
}
return { kind: "figma-snapshot/icons", svgs };
