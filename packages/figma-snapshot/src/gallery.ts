/** `docs/design/figma/index.html`: все кадры снимка с превью и ссылками, для просмотра глазами. */
import { escapeHtml } from "./page.ts";

export interface GalleryItem {
  readonly name: string;
  readonly dir: string;
  readonly width: number;
  readonly height: number;
  readonly clone: boolean;
  readonly screenshot: boolean;
  readonly match?: number;
}

export interface GallerySection {
  readonly title: string;
  readonly items: readonly GalleryItem[];
}

const STYLE = `
:root{--canvas:#f2f5f7;--surface:#fff;--ink:#151d23;--muted:#4d5b65;--line:#d4dce1;--primary:#1f5f77}
*{box-sizing:border-box}
body{margin:0;background:var(--canvas);color:var(--ink);font:15px/22px Onest,system-ui,sans-serif}
main{max-width:1280px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:26px;line-height:32px;margin:0 0 8px}
h2{font-size:20px;line-height:26px;margin:40px 0 16px}
p{color:var(--muted);margin:0}
ul{list-style:none;padding:0;margin:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:16px}
li{background:var(--surface);border-radius:12px;overflow:hidden;display:flex;flex-direction:column}
.thumb{display:block;height:180px;background:var(--canvas);border-bottom:1px solid var(--line)}
.thumb img{width:100%;height:100%;object-fit:contain;object-position:top}
.body{padding:12px 16px 16px;display:flex;flex-direction:column;gap:4px}
.name{font-weight:600}
.meta{color:var(--muted);font-size:13px;line-height:18px}
.links{display:flex;gap:12px;font-size:14px}
a{color:var(--primary)}
`;

function card(item: GalleryItem): string {
  const match = item.match === undefined ? "" : `, сходство ${item.match.toFixed(1)} %`;
  const links = [
    item.clone ? `<a href="${item.dir}/index.html">Клон</a>` : "",
    item.screenshot ? `<a href="${item.dir}/figma.png">Эталон</a>` : "",
    `<a href="${item.dir}/notes.md">Заметки</a>`,
  ].filter(Boolean);
  const thumb = item.screenshot
    ? `<a class="thumb" href="${item.dir}/${item.clone ? "index.html" : "figma.png"}"><img loading="lazy" src="${item.dir}/figma.png" alt=""></a>`
    : "";
  return `<li>${thumb}<div class="body"><span class="name">${escapeHtml(item.name)}</span><span class="meta">${item.width}×${item.height}${match}</span><span class="links">${links.join("")}</span></div></li>`;
}

/** Галерея снимка: разделы страниц, превью кадров, ссылки на клон, эталон и заметки. */
export function galleryPage(generated: string, sections: readonly GallerySection[]): string {
  const body = sections
    .filter((section) => section.items.length > 0)
    .map(
      (section) =>
        `<h2>${escapeHtml(section.title)}</h2>\n<ul>${section.items.map(card).join("\n")}</ul>`,
    );
  return [
    "<!doctype html>",
    '<html lang="ru">',
    '<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">',
    "<title>Макеты Figma — снимок</title>",
    '<link rel="stylesheet" href="_shared/fonts.css">',
    `<style>${STYLE}</style></head>`,
    "<body><main>",
    "<h1>Макеты Figma: снимок для работы без доступа к Figma</h1>",
    `<p>Снимок от ${escapeHtml(generated)}. Клон — статическая страница по коду из Figma MCP, эталон — скриншот кадра. Как пользоваться — README.md рядом.</p>`,
    ...body,
    "</main></body></html>",
    "",
  ].join("\n");
}
