/** Markdown снимка: заметка кадра, общий список компонентов, README страницы. */
import type { FrameEntry } from "./manifest.ts";
import type { ComponentNote, StyleNote } from "./notes.ts";

export interface FrameFiles {
  readonly reference: boolean;
  readonly clone: boolean;
  readonly screenshot: boolean;
}

export interface FrameNotesInput {
  readonly fileKey: string;
  readonly frame: FrameEntry;
  readonly files: FrameFiles;
  readonly annotations: readonly string[];
  readonly styles: readonly StyleNote[];
  readonly components: readonly ComponentNote[];
  /** Ресурсы, которых нет ни в ответе MCP, ни в выгрузке SVG. */
  readonly missingAssets: readonly string[];
  /** Части разреженного кадра: MCP не отдал код целиком, клоны лежат по частям. */
  readonly parts?: readonly FrameEntry[];
}

const GENERATED = "Сгенерировано `packages/figma-snapshot`, руками не править.";

/** Ссылка на узел в файле Figma: откроется только у тех, у кого есть доступ. */
export function figmaUrl(fileKey: string, nodeId: string): string {
  return `https://www.figma.com/design/${fileKey}/?node-id=${nodeId.replaceAll(":", "-")}`;
}

export const anchor = (nodeId: string) => nodeId.replace(/[^0-9a-z]+/gi, "-");

function firstSentence(text: string): string {
  const line = text.split("\n")[0] ?? "";
  const end = line.search(/[.!?](\s|$)/);
  return end === -1 ? line : line.slice(0, end + 1);
}

/** `notes.md` кадра: узел, размер, файлы, аннотации, стили и компоненты. */
export function frameNotes(input: FrameNotesInput): string {
  const { frame, files } = input;
  const fileLines = [
    files.screenshot
      ? "- [`figma.png`](figma.png) — эталон: скриншот кадра из Figma в масштабе 1:1."
      : "",
    files.clone
      ? "- [`index.html`](index.html) — клон: статическая страница, сверенная с эталоном попиксельно."
      : "",
    files.reference
      ? "- [`reference.tsx`](reference.tsx) — код кадра из Figma MCP (React + Tailwind), ресурсы в `assets/`."
      : "",
    ...(input.parts ?? []).map(
      (part) =>
        `- [\`${part.dir.split("/").pop()}/\`](${part.dir.split("/").pop()}/notes.md) — часть кадра: ${part.name}.`,
    ),
  ].filter(Boolean);
  const sections = [
    `# ${frame.name}`,
    GENERATED,
    [
      `- Узел Figma: [${frame.id}](${figmaUrl(input.fileKey, frame.id)}). Ссылка откроется только у тех, у кого есть доступ к файлу.`,
      `- Размер: ${frame.width} × ${frame.height} px.`,
      ...(frame.caption ? [`- Подпись раздела: ${frame.caption.replaceAll("\n", " — ")}`] : []),
    ].join("\n"),
    `## Файлы\n\n${fileLines.join("\n")}`,
  ];
  if (input.missingAssets.length > 0) {
    sections.push(
      `## Нет в снимке\n\nMCP не вернул эти ресурсы, на клоне на их месте пусто: ${input.missingAssets.map((name) => `\`${name}\``).join(", ")}.`,
    );
  }
  if (input.annotations.length > 0)
    sections.push(`## Аннотации из макета\n\n${input.annotations.join("\n\n---\n\n")}`);
  if (input.styles.length > 0) {
    sections.push(
      `## Стили\n\n${input.styles.map((style) => `- \`${style.name}\` — ${style.value}`).join("\n")}`,
    );
  }
  if (input.components.length > 0) {
    const lines = input.components.map(
      (component) =>
        `- [${component.name}](../../components.md#${anchor(component.nodeId)}) — ${firstSentence(component.description)}`,
    );
    sections.push(
      `## Компоненты\n\nПолные описания — в [components.md](../../components.md).\n\n${lines.join("\n")}`,
    );
  }
  return `${sections.join("\n\n")}\n`;
}

/** Все описания компонентов из макета, по имени. */
export function componentsMarkdown(fileKey: string, components: readonly ComponentNote[]): string {
  const sorted = [...components].sort((a, b) => a.name.localeCompare(b.name, "ru"));
  const body = sorted.map(
    (component) =>
      `<a id="${anchor(component.nodeId)}"></a>\n\n## ${component.name}\n\nУзел [${component.nodeId}](${figmaUrl(fileKey, component.nodeId)}).\n\n${component.description}`,
  );
  return `# Компоненты макета\n\n${GENERATED} Описания написаны в Figma у самих компонентов: как использовать, какие варианты и ограничения.\n\n${body.join("\n\n")}\n`;
}

export interface PageRow {
  readonly frame: FrameEntry;
  readonly files: FrameFiles;
  readonly match?: number;
}

/** Текст для ячейки Markdown-таблицы: `|` разорвал бы строку таблицы. */
const cell = (text: string) => text.replaceAll("|", "\\|");

/** README страницы: таблица кадров и общие заметки страницы. */
export function pageReadme(
  title: string,
  pageDir: string,
  rows: readonly PageRow[],
  notes: readonly string[],
): string {
  const link = (row: PageRow, file: string, present: boolean) =>
    present ? `[${file}](${row.frame.dir.slice(pageDir.length + 1)}/${file})` : "—";
  const table = rows.map(
    (row) =>
      `| ${cell(row.frame.name)} | ${row.frame.width}×${row.frame.height} | ${link(row, "index.html", row.files.clone)} | ${link(row, "figma.png", row.files.screenshot)} | ${link(row, "notes.md", true)} | ${row.match === undefined ? "—" : `${row.match.toFixed(1)} %`} |`,
  );
  const sections = [
    `# ${title}`,
    GENERATED,
    `| Кадр | Размер | Клон | Эталон | Заметки | Сходство |\n|---|---|---|---|---|---|\n${table.join("\n")}`,
  ];
  if (notes.length > 0) sections.push(`## Общие заметки страницы\n\n${notes.join("\n\n---\n\n")}`);
  return `${sections.join("\n\n")}\n`;
}
