/**
 * Чистка эталонного кода из Figma MCP (`get_design_context`) перед сохранением в репозиторий.
 *
 * MCP оборачивает code syntax переменной в `var(<имя>, <значение>)`. У переменных отступов
 * в нашем файле code syntax — выражение `calc(var(--spacing)*4)`, и MCP выдаёт невалидный
 * `var(--calc(var(--spacing)*4),16px)`: браузер отбрасывает такое свойство целиком.
 */

const FIGMA_ASSET = /const (\w+) = "(https:\/\/www\.figma\.com\/api\/mcp\/asset\/[^"]+\.(\w+))";/g;
const FONT_WITH_STYLE = /font-\[(['"])([^'":]+):[^'"]*\1\]/g;
const CALC_VARIABLE = "var(--calc(";

export interface AssetDownload {
  readonly name: string;
  readonly url: string;
}

export interface MissingAsset {
  readonly variable: string;
  readonly nodeId: string;
}

/** Индекс закрывающей скобки для открывающей в позиции `open`. */
function closingParen(text: string, open: number): number {
  let depth = 0;
  for (let index = open; index < text.length; index += 1) {
    if (text[index] === "(") depth += 1;
    if (text[index] === ")") {
      depth -= 1;
      if (depth === 0) return index;
    }
  }
  throw new Error(`Нет закрывающей скобки после позиции ${open}`);
}

/** `var(--calc(X),Y)` → `calc(X)`: выражение переменной и есть её значение в коде. */
function fixCalcVariables(code: string): string {
  let result = "";
  let rest = code;
  for (let start = rest.indexOf(CALC_VARIABLE); start !== -1; start = rest.indexOf(CALC_VARIABLE)) {
    const calcOpen = start + "var(--calc".length;
    const calcClose = closingParen(rest, calcOpen);
    const varClose = closingParen(rest, start + "var".length);
    result += `${rest.slice(0, start)}calc${rest.slice(calcOpen, calcClose + 1)}`;
    rest = rest.slice(varClose + 1);
  }
  return result + rest;
}

/**
 * MCP пишет слоту высоту из главного компонента (`h-[128px]`), хотя в экземпляре слот
 * растёт по вложенным строкам. Высоту снимаем только у обёртки, где внутри ровно `{children}`.
 */
const FIXED_SLOT =
  /(<div className="(?:[^"]*?\s)?)h-\[[\d.]+px\]\s?([^"]*"[^>]*>\s*\{children\}\s*<\/div>)/g;
/(<div className="[^"]*?)\s?\bh-\[[\d.]+px\]([^"]*"[^>]*>\s*\{children\}\s*<\/div>)/g;

/**
 * Текст Figma «по ширине содержимого» с максимальной шириной растёт до максимума и дальше
 * переносится. MCP добавляет ему `whitespace-nowrap`, и строка вылезает за максимум.
 */
const NOWRAP_WITH_MAX = /className="([^"]*\bmax-w-\[[^"]*)"/g;

function wrapWithinMaxWidth(code: string): string {
  return code.replace(NOWRAP_WITH_MAX, (_match, classes: string) => {
    const kept = classes.split(" ").filter((name) => name !== "whitespace-nowrap");
    return `className="${kept.join(" ")}"`;
  });
}

/** Убирает высоту слота; если класс стоял последним, убирает и пробел перед кавычкой. */
function releaseSlot(_match: string, before: string, after: string): string {
  return after.startsWith('"') ? `${before.trimEnd()}${after}` : `${before}${after}`;
}

/** Делает эталонный код валидным для Tailwind и браузера, не меняя вид. */
export function sanitizeCode(code: string): string {
  const valid = fixCalcVariables(code).replace(FONT_WITH_STYLE, "font-[$1$2$1]");
  return wrapWithinMaxWidth(valid.replace(FIXED_SLOT, releaseSlot));
}

/** Заменяет временные адреса MCP (живут 7 дней) путями `./assets/<имя>`. */
export function localizeAssets(code: string): { code: string; downloads: AssetDownload[] } {
  const downloads: AssetDownload[] = [];
  const localized = code.replace(
    FIGMA_ASSET,
    (_match, variable: string, url: string, ext: string) => {
      const name = `${variable}.${ext}`;
      downloads.push({ name, url });
      return `const ${variable} = "./assets/${name}";`;
    },
  );
  return { code: localized, downloads };
}

function idAlternatives(attributes: string): string[] {
  const dataNode = attributes.match(/data-node-id="([^"]+)"/);
  if (dataNode?.[1]) return [dataNode[1]];
  const idExpression = attributes.match(/\bid=\{([^}]*)\}|\bid="node-([^"]+)"/);
  if (!idExpression) return [];
  if (idExpression[2]) return [idExpression[2].replaceAll("_", ":")];
  return [...(idExpression[1] ?? "").matchAll(/"node-([^"]+)"/g)].map((match) =>
    (match[1] ?? "").replaceAll("_", ":"),
  );
}

/** Ближайший предок с узлом Figma: атрибуты последнего открытого тега до позиции. */
function nearestNodeIds(code: string, position: number): string[] {
  const before = code.slice(0, position);
  const tags = [
    ...before.matchAll(/<(?:div|a|p|span)\b[^>]*?(?:data-node-id="[^"]+"|\bid=)[^>]*>/g),
  ];
  for (let index = tags.length - 1; index >= 0; index -= 1) {
    const ids = idAlternatives(tags[index]?.[0] ?? "");
    if (ids.length > 0) return ids;
  }
  return [];
}

/**
 * Ресурсы, которые код использует, но MCP не выдал для них константу. Так бывает у иконок,
 * подменённых внутри экземпляра компонента. Их выгружаем отдельно по узлу Figma.
 */
export function findMissingAssets(code: string): MissingAsset[] {
  const defined = new Set([...code.matchAll(/^const (img\w*) = /gm)].map((match) => match[1]));
  const missing = new Map<string, MissingAsset>();
  for (const source of code.matchAll(/src=\{([^}]+)\}/g)) {
    const variables = [...(source[1] ?? "").matchAll(/\bimg\d*\b/g)].map((match) => match[0]);
    const nodeIds = nearestNodeIds(code, source.index);
    variables.forEach((variable, index) => {
      if (defined.has(variable) || missing.has(variable)) return;
      const nodeId = nodeIds.length === variables.length ? nodeIds[index] : nodeIds[0];
      if (nodeId) missing.set(variable, { variable, nodeId });
    });
  }
  return [...missing.values()];
}
