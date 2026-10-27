/**
 * Тексты снимка в Markdown: описания компонентов и стили из ответа MCP, аннотации макета.
 */

export interface ComponentNote {
  readonly name: string;
  readonly nodeId: string;
  readonly description: string;
}

export interface StyleNote {
  readonly name: string;
  readonly value: string;
}

export interface AnnotationText {
  readonly text: string;
  readonly bold: boolean;
}

const STYLES_PREFIX = "These styles are contained in the design: ";
const STYLE = /\s*([^,]+?): ((?:Font|Effect|Paint|Grid)\((?:[^()]|\([^()]*\))*\))/g;

/** Блок «Component descriptions» из ответа get_design_context. */
export function parseComponents(block: string): ComponentNote[] {
  return block
    .split(/^## /m)
    .slice(1)
    .flatMap((section) => {
      const [name = "", ...rest] = section.split("\n");
      const body = rest.join("\n");
      const nodeId = body.match(/\*\*Node ID:\*\* (\S+)/)?.[1];
      if (!nodeId) return [];
      const description = body.replace(/\*\*Node ID:\*\* \S+/, "").trim();
      return [{ name: name.trim(), nodeId, description }];
    });
}

/** Блок «These styles are contained in the design» из ответа get_design_context. */
export function parseTextStyles(block: string): StyleNote[] {
  const list = block.startsWith(STYLES_PREFIX) ? block.slice(STYLES_PREFIX.length) : block;
  return [...list.matchAll(STYLE)].map((match) => ({
    name: match[1] ?? "",
    value: match[2] ?? "",
  }));
}

/** Аннотация в Markdown: жирные строки — подзаголовки, строки с тире — список. */
export function annotationMarkdown(texts: readonly AnnotationText[]): string {
  const blocks: string[] = [];
  let previousIsList = false;
  for (const { text, bold } of texts) {
    const isList = !bold && text.startsWith("— ");
    const item = bold ? `**${text}**` : isList ? `- ${text.slice(2)}` : text;
    if (isList && previousIsList) blocks[blocks.length - 1] += `\n${item}`;
    else blocks.push(item);
    previousIsList = isList;
  }
  return blocks.join("\n\n");
}
