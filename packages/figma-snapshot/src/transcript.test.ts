import assert from "node:assert/strict";
import { mkdtemp, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { collectToolResults } from "./transcript.ts";

const TOOL = "mcp__plugin_figma_figma__get_design_context";

function line(role: "assistant" | "user", content: unknown[], timestamp?: string): string {
  return JSON.stringify({ type: role, timestamp, message: { role, content } });
}

test("ответ инструмента находится по входу вызова, в том числе вынесенный в файл", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "figma-snapshot-"));
  const saved = path.join(directory, "saved.txt");
  await writeFile(saved, JSON.stringify([{ type: "text", text: "const big = 1;" }]));
  const transcript = path.join(directory, "session.jsonl");
  await writeFile(
    transcript,
    [
      line("assistant", [{ type: "tool_use", id: "a", name: TOOL, input: { nodeId: "121:63" } }]),
      line("user", [
        {
          type: "tool_result",
          tool_use_id: "a",
          content: [
            { type: "text", text: "const small = 1;" },
            { type: "image", source: {} },
            { type: "text", text: "Component descriptions: …" },
          ],
        },
      ]),
      line("assistant", [{ type: "tool_use", id: "b", name: TOOL, input: { nodeId: "122:402" } }]),
      line("user", [
        {
          type: "tool_result",
          tool_use_id: "b",
          content: `Error: result (75,360 characters) exceeds maximum allowed tokens. Output has been saved to ${saved}.\nFormat: JSON array`,
        },
      ]),
      line("assistant", [{ type: "tool_use", id: "c", name: "Bash", input: { command: "ls" } }]),
    ].join("\n"),
  );

  const results = await collectToolResults([transcript], TOOL);

  assert.deepEqual(results, [
    { input: { nodeId: "121:63" }, texts: ["const small = 1;", "Component descriptions: …"] },
    { input: { nodeId: "122:402" }, texts: ["const big = 1;"] },
  ]);
});

test("ответ, сохранённый в файл .json с превью, тоже читается целиком", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "figma-snapshot-"));
  const saved = path.join(directory, "toolu_01.json");
  await writeFile(saved, JSON.stringify([{ type: "text", text: "const full = 1;" }]));
  const transcript = path.join(directory, "session.jsonl");
  await writeFile(
    transcript,
    [
      line("assistant", [{ type: "tool_use", id: "a", name: TOOL, input: { nodeId: "135:369" } }]),
      line("user", [
        {
          type: "tool_result",
          tool_use_id: "a",
          content: `<persisted-output>\nOutput too large (57.1KB). Full output saved to: ${saved}\n\nPreview (first 2KB):\n[{"type":"text","text":"const fu`,
        },
      ]),
    ].join("\n"),
  );

  assert.deepEqual(await collectToolResults([transcript], TOOL), [
    { input: { nodeId: "135:369" }, texts: ["const full = 1;"] },
  ]);
});

test("ответ с ошибкой инструмента пропускается", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "figma-snapshot-"));
  const transcript = path.join(directory, "session.jsonl");
  await writeFile(
    transcript,
    [
      line("assistant", [{ type: "tool_use", id: "a", name: TOOL, input: { nodeId: "1:2" } }]),
      line("user", [{ type: "tool_result", tool_use_id: "a", is_error: true, content: "boom" }]),
    ].join("\n"),
  );

  assert.deepEqual(await collectToolResults([transcript], TOOL), []);
});

test("ответы из разных журналов идут по времени, а не по имени файла", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "figma-snapshot-"));
  const call = (id: string, text: string, timestamp: string) => [
    line("assistant", [{ type: "tool_use", id, name: TOOL, input: { nodeId: "1:2" } }], timestamp),
    line("user", [{ type: "tool_result", tool_use_id: id, content: text }], timestamp),
  ];
  const early = path.join(directory, "b-early.jsonl");
  const late = path.join(directory, "a-late.jsonl");
  await writeFile(early, call("x", "старый", "2026-10-20T10:00:00.000Z").join("\n"));
  await writeFile(late, call("y", "новый", "2026-10-21T10:00:00.000Z").join("\n"));

  const results = await collectToolResults([late, early], TOOL);

  assert.deepEqual(
    results.map((result) => result.texts[0]),
    ["старый", "новый"],
  );
});

test("недописанная последняя строка живого журнала пропускается", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "figma-snapshot-"));
  const transcript = path.join(directory, "session.jsonl");
  await writeFile(
    transcript,
    [
      line("assistant", [{ type: "tool_use", id: "a", name: TOOL, input: { nodeId: "1:2" } }]),
      line("user", [{ type: "tool_result", tool_use_id: "a", content: "код" }]),
      '{"type":"assistant","message":{"content":[{"type":"tool_use"',
    ].join("\n"),
  );

  assert.deepEqual(await collectToolResults([transcript], TOOL), [
    { input: { nodeId: "1:2" }, texts: ["код"] },
  ]);
});

test("пропавший файл с вынесенным ответом — ошибка с путём и журналом", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "figma-snapshot-"));
  const transcript = path.join(directory, "session.jsonl");
  const gone = path.join(directory, "gone.txt");
  await writeFile(
    transcript,
    [
      line("assistant", [{ type: "tool_use", id: "a", name: TOOL, input: { nodeId: "1:2" } }]),
      line("user", [
        { type: "tool_result", tool_use_id: "a", content: `Output has been saved to ${gone}.` },
      ]),
    ].join("\n"),
  );

  await assert.rejects(collectToolResults([transcript], TOOL), (error: Error) => {
    assert.match(error.message, /gone\.txt/);
    assert.match(error.message, /session\.jsonl/);
    return true;
  });
});
