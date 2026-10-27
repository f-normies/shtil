// Истории болезни для прогона с распознаванием (спека #30, раздел 7): PDF с текстовым слоем
// и PNG одной страницы из knowledge/cases/<id>/history.md. Запуск из корня репозитория:
//
//   pnpm exec playwright install chromium   # один раз
//   node eval/extraction/render.mjs
//
// Markdown историй простой: заголовки, абзацы, таблицы и **жирный**. Его разбирает функция
// ниже, без зависимостей. Хеши history.md пишутся в documents/manifest.json: тест сверяет их
// с текущими историями.
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const HERE = dirname(fileURLToPath(import.meta.url));
const CASES = join(HERE, "..", "..", "knowledge", "cases");
const OUT = join(HERE, "documents");
const IDS = ["case-01", "case-08", "case-12", "case-17"];
// Ширина листа A4 при 96 dpi. Масштаб 1,6 даёт картинку около 1270 px в ширину: как у фото
// листа телефоном после обрезки.
const A4_WIDTH = 794;
const SCALE = 1.6;

const escape = (text) =>
  text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
const inline = (text) => escape(text).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
const cells = (line) =>
  line
    .trim()
    .replace(/^\||\|$/g, "")
    .split("|")
    .map((cell) => inline(cell.trim()));

function toHtml(markdown) {
  const blocks = [];
  let paragraph = [];
  let table = [];
  const flush = () => {
    if (paragraph.length) blocks.push(`<p>${paragraph.map(inline).join("<br>")}</p>`);
    if (table.length) {
      const [head, , ...rows] = table;
      const th = cells(head).map((c) => `<th>${c}</th>`).join("");
      const tr = rows.map((r) => `<tr>${cells(r).map((c) => `<td>${c}</td>`).join("")}</tr>`);
      blocks.push(`<table><thead><tr>${th}</tr></thead><tbody>${tr.join("")}</tbody></table>`);
    }
    paragraph = [];
    table = [];
  };
  for (const line of markdown.split("\n")) {
    const heading = line.match(/^(#{1,2}) (.*)$/);
    if (heading) {
      flush();
      blocks.push(`<h${heading[1].length}>${inline(heading[2])}</h${heading[1].length}>`);
    } else if (line.trim().startsWith("|")) {
      if (paragraph.length) flush();
      table.push(line);
    } else if (!line.trim()) {
      flush();
    } else {
      if (table.length) flush();
      paragraph.push(line);
    }
  }
  flush();
  return `<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>
    @page { size: A4; margin: 16mm 18mm; }
    body { font: 10.5pt/1.35 "Times New Roman", serif; color: #000; background: #fff;
           margin: 0; padding: 16mm 18mm; }
    @media print { body { padding: 0; } }
    h1 { font-size: 13pt; margin: 0 0 8pt; } h2 { font-size: 11pt; margin: 10pt 0 4pt; }
    p { margin: 0 0 6pt; }
    table { border-collapse: collapse; margin: 0 0 8pt; }
    th, td { border: 0.5pt solid #000; padding: 1pt 6pt; text-align: left; }
  </style></head><body>${blocks.join("\n")}</body></html>`;
}

mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
const manifest = {};
try {
  for (const id of IDS) {
    const markdown = readFileSync(join(CASES, id, "history.md"), "utf8");
    const page = await browser.newPage({
      viewport: { width: A4_WIDTH, height: 1123 },
      deviceScaleFactor: SCALE,
    });
    await page.setContent(toHtml(markdown));
    await page.pdf({ path: join(OUT, `${id}.pdf`), format: "A4", printBackground: true });
    await page.screenshot({ path: join(OUT, `${id}.png`), fullPage: true });
    await page.close();
    manifest[id] = {
      history_sha256: createHash("sha256").update(markdown).digest("hex"),
      pdf: `${id}.pdf`,
      png: `${id}.png`,
    };
  }
} finally {
  await browser.close();
}
writeFileSync(join(OUT, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`);
