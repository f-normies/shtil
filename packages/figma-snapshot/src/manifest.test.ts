import assert from "node:assert/strict";
import test from "node:test";

import { planSnapshot } from "./manifest.ts";

const node = (id: string, name: string, extra: Record<string, unknown> = {}) => ({
  id,
  name,
  type: "FRAME",
  x: 0,
  y: 0,
  width: 100,
  height: 100,
  ...extra,
});

test("экраны, доски и разделы компонентов попадают в план, аннотации — в заметки", () => {
  const plan = planSnapshot([
    {
      page: { id: "109:15", name: "Врач" },
      nodes: [
        node("121:63", "Врач v4 · 02 Пациенты", { x: 0, width: 1280, height: 832 }),
        node("169:7409", "Аннотация · Врач v4 · 02 Пациенты", { y: 900 }),
        node("175:7409", "Как читать макет врача", { x: -2000 }),
        node("134:279", "Сигналы/Шаг", { type: "COMPONENT" }),
        node("158:16130", "Дневник · Доска состояний графика настроения"),
      ],
    },
    {
      page: { id: "17:2", name: "Компоненты" },
      nodes: [
        node("18:2", "Иконки"),
        node("19:2", "Компоненты", {
          children: [node("19:35", "Кнопка", { type: "COMPONENT_SET" })],
        }),
        node("21:28", "Знак/Врач", { type: "COMPONENT" }),
        node("116:109", "Врач v4", {
          children: [
            node("116:110", "Шапка доски"),
            node("116:113", "Раздел · Атомы", { caption: "Атомы\nЗначок статуса." }),
            node("138:10494", "Раздел · Уровни шкалы", {
              children: [node("138:10498", "Компактный")],
            }),
          ],
        }),
      ],
    },
  ]);

  assert.deepEqual(
    plan.frames.map((frame) => [frame.id, frame.kind, frame.dir]),
    [
      ["121:63", "screen", "doctor/02-patsienty"],
      ["158:16130", "board", "doctor/board-dnevnik-doska-sostoyaniy-grafika"],
      ["18:2", "component", "components/ikonki"],
      ["19:35", "component", "components/base-knopka"],
      ["116:113", "component", "components/doctor-atomy"],
      ["138:10498", "component", "components/doctor-urovni-shkaly-kompaktnyy"],
    ],
  );
  assert.equal(
    plan.frames.find((frame) => frame.id === "116:113")?.caption,
    "Атомы\nЗначок статуса.",
  );
  assert.deepEqual(plan.notes, [
    { page: "Врач", byFrame: { "121:63": ["169:7409"] }, pageLevel: ["175:7409"] },
    { page: "Компоненты", byFrame: {}, pageLevel: [] },
  ]);
});

test("два кадра с одним каталогом — ошибка: второй молча затёр бы первый", () => {
  assert.throws(
    () =>
      planSnapshot([
        {
          page: { id: "109:15", name: "Врач" },
          nodes: [node("1:1", "Врач v4 · 02 Пациенты"), node("1:2", "Врач v4 · 02 Пациенты")],
        },
      ]),
    /doctor\/02-patsienty.*1:1.*1:2/,
  );
});
