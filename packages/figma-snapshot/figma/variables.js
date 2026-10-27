// Скрипт для use_figma (Plugin API). Все локальные переменные со значениями по режимам.
// Псевдоним выгружается как { alias: "<коллекция>/<имя>" }. Результат забирает materialize.ts.
const toHex = (value) => Math.round(value * 255).toString(16).padStart(2, "0");
const collections = await figma.variables.getLocalVariableCollectionsAsync();
const byId = new Map();
for (const variable of await figma.variables.getLocalVariablesAsync()) byId.set(variable.id, variable);
const collectionName = new Map(collections.map((collection) => [collection.id, collection.name]));

function valueOf(value) {
  if (value && typeof value === "object" && value.type === "VARIABLE_ALIAS") {
    const target = byId.get(value.id);
    return { alias: target ? `${collectionName.get(target.variableCollectionId)}/${target.name}` : value.id };
  }
  if (value && typeof value === "object" && "r" in value) {
    const alpha = "a" in value && value.a < 1 ? toHex(value.a) : "";
    return `#${toHex(value.r)}${toHex(value.g)}${toHex(value.b)}${alpha}`;
  }
  return value;
}

return {
  kind: "figma-snapshot/variables",
  collections: collections.map((collection) => ({
    name: collection.name,
    modes: collection.modes.map((mode) => mode.name),
    variables: collection.variableIds.map((id) => {
      const variable = byId.get(id);
      return {
        name: variable.name,
        type: variable.resolvedType,
        web: variable.codeSyntax.WEB ?? null,
        description: variable.description || undefined,
        values: collection.modes.map((mode) => valueOf(variable.valuesByMode[mode.modeId])),
      };
    }),
  })),
};
