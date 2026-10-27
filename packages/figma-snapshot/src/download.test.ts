import assert from "node:assert/strict";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { download } from "./download.ts";

function responses(...bodies: (string | number | Error)[]): typeof fetch & { calls: number } {
  const get = Object.assign(
    async () => {
      const body = bodies[Math.min(get.calls++, bodies.length - 1)];
      if (body instanceof Error) throw body;
      if (typeof body === "number") return new Response("", { status: body });
      return new Response(body, { status: 200 });
    },
    { calls: 0 },
  );
  return get as unknown as typeof fetch & { calls: number };
}

async function target(name = "figma.png"): Promise<string> {
  return path.join(await mkdtemp(path.join(os.tmpdir(), "figma-snapshot-")), name);
}

test("пустой ответ 200 повторяется: Figma отдаёт его, пока рендер скриншота не готов", async () => {
  const file = await target();

  await download("https://example.test/x.png", file, {
    fetch: responses("", "", "png"),
    delayMs: 0,
  });

  assert.equal(await readFile(file, "utf8"), "png");
});

test("файл с тем же адресом не перекачивается, с новым адресом — перекачивается", async () => {
  const same = await target();
  const changed = await target();
  await writeFile(same, "old");
  await writeFile(changed, "old");
  const ledger = new Map([
    [same, "https://example.test/a.png"],
    [changed, "https://example.test/old.png"],
  ]);

  await download("https://example.test/a.png", same, {
    fetch: responses("new"),
    delayMs: 0,
    ledger,
  });
  await download("https://example.test/b.png", changed, {
    fetch: responses("new"),
    delayMs: 0,
    ledger,
  });

  assert.equal(await readFile(same, "utf8"), "old");
  assert.equal(await readFile(changed, "utf8"), "new");
  assert.equal(ledger.get(changed), "https://example.test/b.png");
});

test("файл без записи в журнале скачиваний перекачивается: снимок мог устареть", async () => {
  const file = await target();
  await writeFile(file, "old");

  await download("https://example.test/a.png", file, { fetch: responses("new"), delayMs: 0 });

  assert.equal(await readFile(file, "utf8"), "new");
});

test("после всех попыток с пустым ответом — ошибка, файл не создаётся", async () => {
  const file = await target();

  await assert.rejects(
    download("https://example.test/x.png", file, { fetch: responses(""), delayMs: 0 }),
    /пустой ответ/,
  );
  await assert.rejects(readFile(file));
});

test("ответ 4xx — сразу ошибка без повторов: адрес истёк", async () => {
  const file = await target();
  const get = responses(403);

  await assert.rejects(
    download("https://example.test/x.png", file, { fetch: get, delayMs: 0 }),
    /403/,
  );
  assert.equal(get.calls, 1);
});

test("сетевой сбой повторяется", async () => {
  const file = await target();

  await download("https://example.test/x.png", file, {
    fetch: responses(new TypeError("fetch failed"), "png"),
    delayMs: 0,
  });

  assert.equal(await readFile(file, "utf8"), "png");
});
