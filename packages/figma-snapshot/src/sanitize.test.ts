import assert from "node:assert/strict";
import test from "node:test";

import { findMissingAssets, localizeAssets, sanitizeCode } from "./sanitize.ts";

test("переменная Figma с выражением calc становится валидным calc", () => {
  const code =
    'className="px-[var(--calc(var(--spacing)*4),16px)] gap-[var(--calc(var(--spacing)*2),8px)]"';

  assert.equal(
    sanitizeCode(code),
    'className="px-[calc(var(--spacing)*4)] gap-[calc(var(--spacing)*2)]"',
  );
});

test("скругление-пилюля из calc(infinity) остаётся валидным", () => {
  const code = 'className="rounded-[var(--calc(infinity*1px),999px)]"';

  assert.equal(sanitizeCode(code), 'className="rounded-[calc(infinity*1px)]"');
});

test("обычная переменная с запасным значением не меняется", () => {
  const code = 'className="bg-[var(--color-surface,white)] text-[color:var(--color-ink,#151d23)]"';

  assert.equal(sanitizeCode(code), code);
});

test("начертание в имени шрифта убирается, вес задаёт font-semibold", () => {
  const code = `className="font-['Onest:SemiBold'] font-semibold" x='font-["Onest:Regular"]'`;

  assert.equal(sanitizeCode(code), `className="font-['Onest'] font-semibold" x='font-["Onest"]'`);
});

test("временные адреса ресурсов заменяются локальными путями", () => {
  const code = [
    'const img = "https://www.figma.com/api/mcp/asset/467b02e8-2681-4de5-999f-ad80c1ae5df2.svg";',
    'const img3 = "https://www.figma.com/api/mcp/asset/d049ae7f-1694-430a-96b1-1d8a12dcff9b.png";',
    "<img src={img} /><img src={img3} />",
  ].join("\n");

  const result = localizeAssets(code);

  assert.equal(
    result.code,
    [
      'const img = "./assets/img.svg";',
      'const img3 = "./assets/img3.png";',
      "<img src={img} /><img src={img3} />",
    ].join("\n"),
  );
  assert.deepEqual(result.downloads, [
    {
      name: "img.svg",
      url: "https://www.figma.com/api/mcp/asset/467b02e8-2681-4de5-999f-ad80c1ae5df2.svg",
    },
    {
      name: "img3.png",
      url: "https://www.figma.com/api/mcp/asset/d049ae7f-1694-430a-96b1-1d8a12dcff9b.png",
    },
  ]);
});

test("ресурс без константы находится вместе с узлом Figma, где он стоит", () => {
  const code = [
    'const img7 = "./assets/img7.svg";',
    '<div className="relative" data-node-id="I193:16694;119:224" data-name="Значок">',
    '  <img alt="" src={img1} />',
    "</div>",
    '<div id={isA ? "node-156_1415" : "node-156_1410"}>',
    '  <img alt="" src={isA ? img4 : img7} />',
    "</div>",
  ].join("\n");

  assert.deepEqual(findMissingAssets(code), [
    { variable: "img1", nodeId: "I193:16694;119:224" },
    { variable: "img4", nodeId: "156:1415" },
  ]);
});

test("слот компонента не держит высоту главного компонента: экземпляр растёт по содержимому", () => {
  const code = [
    '<div className="bg-[var(--color-surface,white)] h-[128px] relative w-full" data-name="Строки">',
    "  {children}",
    "</div>",
    '<div className="h-[44px] relative w-[120px]" data-name="График">',
    "  {children || <V8 />}",
    "</div>",
  ].join("\n");

  assert.equal(
    sanitizeCode(code),
    [
      '<div className="bg-[var(--color-surface,white)] relative w-full" data-name="Строки">',
      "  {children}",
      "</div>",
      '<div className="h-[44px] relative w-[120px]" data-name="График">',
      "  {children || <V8 />}",
      "</div>",
    ].join("\n"),
  );
});

test("текст с максимальной шириной переносится: whitespace-nowrap снимается", () => {
  const code =
    '<p className="leading-[20px] max-w-[216px] shrink-0 text-[14px] whitespace-nowrap">Текст</p>';

  assert.equal(
    sanitizeCode(code),
    '<p className="leading-[20px] max-w-[216px] shrink-0 text-[14px]">Текст</p>',
  );
});

test("у слота снимается только h-[…]: min-h и max-h остаются", () => {
  const code = [
    '<div className="min-h-[40px] h-[128px] max-h-[600px] w-full" data-name="Строки">',
    "  {children}",
    "</div>",
    '<div className="min-h-[40px] w-full" data-name="Слот">',
    "  {children}",
    "</div>",
  ].join("\n");

  assert.equal(
    sanitizeCode(code),
    [
      '<div className="min-h-[40px] max-h-[600px] w-full" data-name="Строки">',
      "  {children}",
      "</div>",
      '<div className="min-h-[40px] w-full" data-name="Слот">',
      "  {children}",
      "</div>",
    ].join("\n"),
  );
});

test("высота слота последним классом снимается без пробела перед кавычкой", () => {
  assert.equal(
    sanitizeCode('<div className="w-full h-[128px]" data-name=" Строки">{children}</div>'),
    '<div className="w-full" data-name=" Строки">{children}</div>',
  );
});
