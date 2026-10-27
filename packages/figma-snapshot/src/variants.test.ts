import assert from "node:assert/strict";
import test from "node:test";

import { parseVariants, variantBoard, variantProps } from "./variants.ts";

const XML = [
  '<frame id="19:35" name="Кнопка" x="40" y="40" width="638" height="144">',
  '  <symbol id="19:3" name="Вид=Основная, Размер=Врач" x="16" y="16" width="85" height="38" />',
  '  <symbol id="19:19" name="Вид=Тихая, Размер=Пациент" x="16" y="72" width="108" height="54" />',
  "</frame>",
  '<frame id="19:63" name="Значок сигнала" x="40" y="232" width="930" height="64">',
  '  <symbol id="19:36" name="Вид=Обязательно" x="16" y="16" width="141" height="32" />',
  "</frame>",
].join("\n");

const CODE = [
  "type ComponentProps = {",
  "  className?: string;",
  "  propValue?: string;",
  "  propValue1?: boolean;",
  '  propValue3?: "Основная" | "Вторичная" | "Тихая";',
  '  propValue4?: "Врач" | "Пациент";',
  "};",
  'export default function Component({ className, propValue = "Кнопка", propValue3 = "Основная", propValue4 = "Врач" }: ComponentProps) {',
].join("\n");

test("варианты набора берутся из метаданных: имя, позиция и свойства", () => {
  assert.deepEqual(parseVariants(XML, "19:35"), [
    {
      id: "19:3",
      x: 16,
      y: 16,
      width: 85,
      height: 38,
      properties: { Вид: "Основная", Размер: "Врач" },
    },
    {
      id: "19:19",
      x: 16,
      y: 72,
      width: 108,
      height: 54,
      properties: { Вид: "Тихая", Размер: "Пациент" },
    },
  ]);
});

test("значение свойства варианта находит проп по типу компонента", () => {
  assert.deepEqual(variantProps(CODE, { Вид: "Тихая", Размер: "Пациент" }), {
    propValue3: "Тихая",
    propValue4: "Пациент",
  });
});

test("доска набора рисует каждый вариант на его месте", () => {
  const source = variantBoard("./reference.tsx", "#f2f5f7", 638, 144, [
    {
      left: 16,
      top: 72,
      width: 108,
      height: 54,
      props: { propValue3: "Тихая", propValue4: "Пациент" },
    },
  ]);

  assert.match(source, /import Component from "\.\/reference\.tsx";/);
  assert.match(source, /background: "#f2f5f7"/);
  assert.match(source, /left: 16, top: 72, width: 108, height: 54/);
  assert.match(source, /<Component \{\.\.\.\{"propValue3":"Тихая","propValue4":"Пациент"\}\} \/>/);
});
