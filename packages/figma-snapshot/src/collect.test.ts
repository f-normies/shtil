import assert from "node:assert/strict";
import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { collect, findTranscripts } from "./collect.ts";

const MCP = "mcp__plugin_figma_figma__";

function call(id: string, tool: string, input: Record<string, unknown>, text: string): string[] {
  return [
    JSON.stringify({
      message: { content: [{ type: "tool_use", id, name: `${MCP}${tool}`, input }] },
    }),
    JSON.stringify({
      message: {
        content: [{ type: "tool_result", tool_use_id: id, content: [{ type: "text", text }] }],
      },
    }),
  ];
}

test("из журнала собираются перечни, код, скриншоты, SVG и тексты аннотаций", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "figma-snapshot-"));
  const transcript = path.join(directory, "session.jsonl");
  const listing = {
    kind: "figma-snapshot/frames",
    page: { id: "17:4", name: "Пациент" },
    nodes: [
      {
        id: "84:5463",
        name: "Аннотация",
        type: "FRAME",
        x: 0,
        y: 0,
        width: 1,
        height: 1,
        texts: [{ text: "Движение", bold: false }],
      },
    ],
  };
  await writeFile(
    transcript,
    [
      ...call("a", "use_figma", {}, JSON.stringify(listing)),
      ...call("b", "get_design_context", { nodeId: "1:2" }, '<frame id="1:2" />'),
      ...call("c", "get_design_context", { nodeId: "1:2" }, "export default function A() {}"),
      ...call("d", "get_design_context", { nodeId: "1:2" }, '<frame id="1:2" />'),
      ...call(
        "e",
        "get_screenshot",
        { nodeId: "1:2" },
        '{"image_url":"https://www.figma.com/api/mcp/asset/x.png","width":1}',
      ),
      ...call(
        "f",
        "use_figma",
        {},
        JSON.stringify({
          kind: "figma-snapshot/svgs",
          svgs: { "I1:2;3:4": { name: "Значок", svg: "<svg/>" } },
        }),
      ),
      ...call(
        "g",
        "use_figma",
        {},
        JSON.stringify({
          kind: "figma-snapshot/texts",
          annotations: [{ id: "170:7409", texts: [{ text: "Сигналы", bold: true }] }],
        }),
      ),
      ...call("h", "use_figma", {}, "not json"),
      ...call("i", "get_metadata", { nodeId: "17:2" }, '<canvas id="17:2" name="Компоненты">'),
    ].join("\n"),
  );

  const collected = await collect([transcript]);

  assert.deepEqual(
    collected.listings.map((item) => item.page.name),
    ["Пациент"],
  );
  assert.deepEqual(collected.designContexts.get("1:2"), ["export default function A() {}"]);
  assert.equal(collected.screenshots.get("1:2"), "https://www.figma.com/api/mcp/asset/x.png");
  assert.deepEqual(collected.svgs.get("I1:2;3:4"), { name: "Значок", svg: "<svg/>" });
  assert.deepEqual(collected.annotationTexts.get("84:5463"), [{ text: "Движение", bold: false }]);
  assert.deepEqual(collected.annotationTexts.get("170:7409"), [{ text: "Сигналы", bold: true }]);
  assert.deepEqual(collected.metadata, ['<canvas id="17:2" name="Компоненты">']);
});

test("журналы ищутся в каталоге сессии вместе с субагентами, файл журнала берётся как есть", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "figma-snapshot-"));
  const main = path.join(directory, "session.jsonl");
  const subagent = path.join(directory, "session", "subagents", "agent-a.jsonl");
  await writeFile(main, "");
  await mkdir(path.dirname(subagent), { recursive: true });
  await writeFile(subagent, "");
  await writeFile(path.join(directory, "session", "subagents", "agent-a.meta.json"), "{}");

  assert.deepEqual(await findTranscripts(main), [main]);
  assert.deepEqual(await findTranscripts(path.join(directory, "session")), [subagent]);
});

test("id из адреса Figma приводится к виду 1:2, скриншот берётся только с хоста Figma", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "figma-snapshot-"));
  const transcript = path.join(directory, "session.jsonl");
  await writeFile(
    transcript,
    [
      ...call("a", "get_design_context", { nodeId: "121-63" }, "export default function A() {}"),
      ...call(
        "b",
        "get_screenshot",
        { nodeId: "121-63" },
        '{"image_url":"https://evil.example/x.png"}',
      ),
      ...call(
        "c",
        "get_screenshot",
        { nodeId: "1:2" },
        '{"image_url":"https://www.figma.com/api/mcp/asset/y.png"}',
      ),
    ].join("\n"),
  );

  const collected = await collect([transcript]);

  assert.deepEqual([...collected.designContexts.keys()], ["121:63"]);
  assert.deepEqual(
    [...collected.screenshots],
    [["1:2", "https://www.figma.com/api/mcp/asset/y.png"]],
  );
});
