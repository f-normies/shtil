import assert from "node:assert/strict";
import test from "node:test";

import { clonePage, tailwindInput } from "./page.ts";

test("страница клона задаёт размер кадра и подключает общий шрифт", () => {
  const html = clonePage({
    title: "Врач v4 · 02 Пациенты",
    nodeId: "121:63",
    width: 1280,
    height: 832,
    css: ".x{color:red}",
    markup: '<div class="x">Пациенты</div>',
    fontsHref: "../../_shared/fonts.css",
  });

  assert.match(html, /^<!doctype html>/);
  assert.match(html, /<title>Врач v4 · 02 Пациенты — клон макета<\/title>/);
  assert.match(html, /<link rel="stylesheet" href="\.\.\/\.\.\/_shared\/fonts\.css">/);
  assert.match(html, /#frame\{width:1280px;height:832px;/);
  assert.match(html, /<div id="frame" data-node-id="121:63"><div class="x">Пациенты<\/div><\/div>/);
});

test("название кадра экранируется в заголовке", () => {
  const html = clonePage({
    title: "A <b> & C",
    nodeId: "1:2",
    width: 10,
    height: 10,
    css: "",
    markup: "",
    fontsHref: "f.css",
  });

  assert.match(html, /<title>A &lt;b&gt; &amp; C — клон макета<\/title>/);
});

test("Tailwind сканирует только код кадра и не подставляет свои цвета и скругления", () => {
  const css = tailwindInput("../../docs/design/figma/doctor/02-patsienty/reference.tsx");

  assert.match(css, /@import "tailwindcss" source\(none\);/);
  assert.match(
    css,
    /@source "\.\.\/\.\.\/docs\/design\/figma\/doctor\/02-patsienty\/reference\.tsx";/,
  );
  assert.match(css, /--color-\*: initial;/);
  assert.match(css, /--radius-\*: initial;/);
  assert.match(css, /--font-sans: "Onest"/);
});

test("белый и чёрный Tailwind остаются: макет пишет bg-white без переменной Figma", () => {
  const css = tailwindInput("reference.tsx");

  assert.match(css, /--color-white: #fff;/);
  assert.match(css, /--color-black: #000;/);
});
