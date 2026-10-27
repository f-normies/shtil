/**
 * Переменные Figma → CSS. Имя CSS-переменной берём из code syntax (WEB) переменной.
 * Первый режим коллекции — значения по умолчанию в `:root`, остальные режимы — селектор
 * `[data-<коллекция>="<режим>"]`. Это справка к #20, а не готовые токены приложения.
 */
import { slugify } from "./layout.ts";

type Value = string | number | boolean | { alias: string };

export interface FigmaVariable {
  readonly name: string;
  readonly type: string;
  readonly web: string | null;
  readonly description?: string;
  readonly values: readonly Value[];
}

export interface FigmaCollection {
  readonly name: string;
  readonly modes: readonly string[];
  readonly variables: readonly FigmaVariable[];
}

const CSS_NAME = /^var\((--[\w-]+)\)$/;

function cssName(variable: FigmaVariable): string | undefined {
  return variable.web?.match(CSS_NAME)?.[1];
}

/** CSS-переменные из переменных Figma: первый режим — `:root`, остальные — по селектору режима. */
export function tokensCss(collections: readonly FigmaCollection[]): string {
  const byPath = new Map<string, FigmaVariable>();
  for (const collection of collections) {
    for (const variable of collection.variables)
      byPath.set(`${collection.name}/${variable.name}`, variable);
  }

  const literal = (variable: FigmaVariable, value: Value): string => {
    if (typeof value === "object") {
      const target = byPath.get(value.alias);
      const name = target && cssName(target);
      return name ? `var(${name})` : value.alias;
    }
    return variable.type === "FLOAT" ? `${value}px` : String(value);
  };

  const root: string[] = [];
  const modes: string[] = [];
  for (const collection of collections) {
    root.push(`  /* ${collection.name} · ${collection.modes[0] ?? ""} */`);
    for (const variable of collection.variables) {
      const name = cssName(variable);
      const value = variable.values[0];
      if (value === undefined) continue;
      root.push(
        name
          ? `  ${name}: ${literal(variable, value)};`
          : `  /* ${variable.name}: ${String(value)} → ${variable.web ?? "без code syntax"} */`,
      );
    }
    collection.modes.slice(1).forEach((mode, offset) => {
      const lines = collection.variables.flatMap((variable) => {
        const name = cssName(variable);
        const value = variable.values[offset + 1];
        return name && value !== undefined ? [`  ${name}: ${literal(variable, value)};`] : [];
      });
      if (lines.length === 0) return;
      modes.push(`[data-${slugify(collection.name)}="${slugify(mode)}"] {\n${lines.join("\n")}\n}`);
    });
  }

  return [
    "/* Переменные Figma «Клинический компас — дизайн». Сгенерировано figma-snapshot, руками не править. */",
    `:root {\n${root.join("\n")}\n}`,
    ...modes,
    "",
  ].join("\n\n");
}
