/**
 * Ответы инструментов Figma MCP из журналов сессий Claude Code (`~/.claude/projects/…/*.jsonl`).
 *
 * Журнал хранит ответ целиком, даже если модели он был показан обрезанным. Большой ответ
 * Claude Code выносит в отдельный файл и пишет в журнал его путь. Так код кадра попадает
 * в репозиторий байт в байт, без пересказа моделью.
 */
import { readFile } from "node:fs/promises";

// Два вида указателя на вынесенный ответ: «… saved to <путь>.txt.» и «Full output saved to: <путь>.json».
const SAVED_OUTPUT = /(?:Output has been saved to|Full output saved to:) (\S+?\.(?:txt|json))\b/;

export interface ToolResult {
  readonly input: Record<string, unknown>;
  readonly texts: string[];
}

interface ContentBlock {
  type?: string;
  text?: string;
  id?: string;
  name?: string;
  input?: Record<string, unknown>;
  tool_use_id?: string;
  is_error?: boolean;
  content?: string | ContentBlock[];
}

interface TranscriptLine {
  readonly timestamp: string;
  readonly blocks: ContentBlock[];
}

/** Строка журнала. Недописанная последняя строка живой сессии не разбирается — пропускаем её. */
function parseLine(line: string): TranscriptLine | undefined {
  if (!line.trim()) return undefined;
  let record: { timestamp?: string; message?: { content?: unknown } };
  try {
    record = JSON.parse(line) as typeof record;
  } catch {
    return undefined;
  }
  const content = record.message?.content;
  return {
    timestamp: record.timestamp ?? "",
    blocks: Array.isArray(content) ? (content as ContentBlock[]) : [],
  };
}

async function textsOf(content: string | ContentBlock[] | undefined): Promise<string[]> {
  if (typeof content === "string") {
    const saved = content.match(SAVED_OUTPUT)?.[1];
    if (!saved) return [content];
    return textsOf(JSON.parse(await readFile(saved, "utf8")) as ContentBlock[]);
  }
  return (content ?? []).flatMap((block) =>
    block.type === "text" && block.text !== undefined ? [block.text] : [],
  );
}

/**
 * Все успешные ответы инструмента `toolName` по времени ответа. Порядок важен: повторный
 * вызов для того же узла (в другой сессии или у другого субагента) новее и побеждает.
 */
export async function collectToolResults(
  transcripts: readonly string[],
  toolName: string,
): Promise<ToolResult[]> {
  const results: (ToolResult & { timestamp: string })[] = [];
  for (const transcript of transcripts) {
    const calls = new Map<string, Record<string, unknown>>();
    for (const line of (await readFile(transcript, "utf8")).split("\n")) {
      const parsed = parseLine(line);
      for (const block of parsed?.blocks ?? []) {
        if (block.type === "tool_use" && block.name === toolName && block.id) {
          calls.set(block.id, block.input ?? {});
        }
        const input = block.tool_use_id ? calls.get(block.tool_use_id) : undefined;
        if (block.type !== "tool_result" || !input || block.is_error) continue;
        try {
          results.push({
            input,
            texts: await textsOf(block.content),
            timestamp: parsed?.timestamp ?? "",
          });
        } catch (error) {
          throw new Error(
            `Не прочитан ответ ${toolName} из журнала ${transcript}: ${String(error)}`,
          );
        }
      }
    }
  }
  results.sort((a, b) => a.timestamp.localeCompare(b.timestamp));
  return results.map(({ input, texts }) => ({ input, texts }));
}
