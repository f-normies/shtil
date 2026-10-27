/**
 * Клон набора вариантов (COMPONENT_SET). MCP отдаёт одну функцию компонента с пропсами
 * вариантов, а `export default` рисует только вариант по умолчанию. Доска набора
 * раскладывает каждый вариант на его место из метаданных; пропсы берутся из имени варианта
 * («Вид=Основная, Размер=Врач») по типам пропсов компонента.
 */

export interface Variant {
  readonly id: string;
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
  readonly properties: Record<string, string>;
}

export interface BoardItem {
  readonly left: number;
  readonly top: number;
  readonly width: number;
  readonly height: number;
  readonly props: Record<string, string>;
}

const NODE =
  /^(\s*)<(\w+) id="([^"]+)" name="([^"]+)" x="([^"]+)" y="([^"]+)" width="([^"]+)" height="([^"]+)"/;

function properties(name: string): Record<string, string> {
  return Object.fromEntries(
    name.split(", ").flatMap((pair) => {
      const [key, value] = pair.split("=");
      return key && value ? [[key, value]] : [];
    }),
  );
}

/** Прямые потомки набора `setId` в XML метаданных get_metadata. */
export function parseVariants(xml: string, setId: string): Variant[] {
  const lines = xml.split("\n");
  const start = lines.findIndex((line) => line.match(NODE)?.[3] === setId);
  if (start === -1) return [];
  const indent = lines[start]?.match(NODE)?.[1]?.length ?? 0;
  const variants: Variant[] = [];
  for (const line of lines.slice(start + 1)) {
    const match = line.match(NODE);
    const depth = line.length - line.trimStart().length;
    if (depth <= indent) break;
    if (!match || depth !== indent + 2) continue;
    const [, , , id = "", name = "", x = "0", y = "0", width = "0", height = "0"] = match;
    variants.push({
      id,
      x: Number(x),
      y: Number(y),
      width: Number(width),
      height: Number(height),
      properties: properties(name),
    });
  }
  return variants;
}

/** Значения свойств варианта → пропсы `export default` по объединениям строк в его типе. */
export function variantProps(
  code: string,
  variant: Record<string, string>,
): Record<string, string> {
  const typeName = code.match(/export default function \w+\(\{[^}]*\}: (\w+)\)/)?.[1];
  const body = typeName
    ? (code.match(new RegExp(`type ${typeName} = \\{([^}]*)\\};`))?.[1] ?? "")
    : "";
  const unions = [...body.matchAll(/(\w+)\?: ("[^"]+"(?: \| "[^"]+")*);/g)].map((match) => ({
    prop: match[1] ?? "",
    values: [...(match[2] ?? "").matchAll(/"([^"]+)"/g)].map((value) => value[1]),
  }));
  const props: Record<string, string> = {};
  for (const value of Object.values(variant)) {
    const owner = unions.find((union) => union.values.includes(value) && !(union.prop in props));
    if (owner) props[owner.prop] = value;
  }
  return props;
}

/** TSX-модуль доски набора: фон набора и варианты на своих местах. */
export function variantBoard(
  reference: string,
  background: string,
  width: number,
  height: number,
  items: readonly BoardItem[],
): string {
  const cells = items.map(
    (item) =>
      `      <div style={{ position: "absolute", left: ${item.left}, top: ${item.top}, width: ${item.width}, height: ${item.height} }}><Component {...${JSON.stringify(item.props)}} /></div>`,
  );
  return [
    `import Component from ${JSON.stringify(reference)};`,
    "export default function Board() {",
    "  return (",
    `    <div style={{ position: "relative", width: ${width}, height: ${height}, background: ${JSON.stringify(background)} }}>`,
    ...cells,
    "    </div>",
    "  );",
    "}",
    "",
  ].join("\n");
}
