import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import type { Collected } from "./collect.ts";
import type { FrameEntry } from "./manifest.ts";
import { materialize } from "./materialize.ts";

const ASSET = "https://www.figma.com/api/mcp/asset/a.svg";
const SHOT = "https://www.figma.com/api/mcp/asset/shot.png";

const FRAME: FrameEntry = {
  id: "1:2",
  name: "Врач v4 · 02 Пациенты",
  page: "Врач",
  kind: "screen",
  dir: "doctor/02-patsienty",
  width: 10,
  height: 10,
};

function collected(): Collected {
  const code = [
    `const img = "${ASSET}";`,
    "export default function Frame() {",
    "  return (",
    '    <div data-node-id="1:2">',
    '      <img alt="" src={img} />',
    '      <div data-node-id="I5:6;7:8" data-name="Значок">',
    '        <img alt="" src={img1} />',
    "      </div>",
    "    </div>",
    "  );",
    "}",
  ].join("\n");
  return {
    listings: [],
    annotationTexts: new Map(),
    designContexts: new Map([["1:2", [code]]]),
    screenshots: new Map([["1:2", SHOT]]),
    svgs: new Map([["I5:6;7:8", { name: "Иконка/время", svg: "<svg>время</svg>" }]]),
    icons: new Map(),
    metadata: [],
  };
}

function fakeFetch(statuses: Record<string, number> = {}): typeof fetch {
  return (async (url: string | URL | Request) => {
    const key = String(url);
    const status = statuses[key] ?? 200;
    return new Response(status === 200 ? `тело ${key}` : "", { status });
  }) as typeof fetch;
}

test("кадр: код с локальными ресурсами, SVG подменённой иконки, без устаревших файлов", async () => {
  const out = await mkdtemp(path.join(os.tmpdir(), "figma-snapshot-"));
  const assets = path.join(out, FRAME.dir, "assets");
  await mkdir(assets, { recursive: true });
  await writeFile(path.join(assets, "img9.svg"), "от прошлого снимка");
  const ledger = new Map<string, string>();

  const result = await materialize({
    out,
    fileKey: "KEY",
    collected: collected(),
    frames: [FRAME],
    notes: [],
    fetch: fakeFetch(),
    ledger,
  });

  const reference = await readFile(path.join(out, FRAME.dir, "reference.tsx"), "utf8");
  assert.match(reference, /const img = "\.\/assets\/img\.svg";/);
  assert.match(reference, /const img1 = "\.\/assets\/img1\.svg";/);
  assert.equal(await readFile(path.join(assets, "img.svg"), "utf8"), `тело ${ASSET}`);
  assert.equal(await readFile(path.join(assets, "img1.svg"), "utf8"), "<svg>время</svg>");
  assert.equal(existsSync(path.join(assets, "img9.svg")), false);
  assert.equal(await readFile(path.join(out, FRAME.dir, "figma.png"), "utf8"), `тело ${SHOT}`);
  assert.equal(ledger.get(path.join(out, FRAME.dir, "figma.png")), SHOT);
  assert.deepEqual(result.problems, []);
  assert.deepEqual(result.frames[0]?.files, { reference: true, clone: true, screenshot: true });
});

test("неудачное скачивание попадает в отчёт и не обрывает снимок", async () => {
  const out = await mkdtemp(path.join(os.tmpdir(), "figma-snapshot-"));

  const result = await materialize({
    out,
    fileKey: "KEY",
    collected: collected(),
    frames: [FRAME],
    notes: [],
    fetch: fakeFetch({ [SHOT]: 403 }),
    ledger: new Map(),
  });

  assert.equal(result.problems.length, 1);
  assert.match(result.problems[0] ?? "", /figma\.png.*403/);
  assert.equal(existsSync(path.join(out, FRAME.dir, "reference.tsx")), true);
  assert.deepEqual(result.frames[0]?.files.screenshot, false);
});
