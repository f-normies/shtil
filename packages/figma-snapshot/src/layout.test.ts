import assert from "node:assert/strict";
import test from "node:test";

import { assignAnnotations, frameDir, slugify } from "./layout.ts";

test("имя кадра транслитерируется в короткий путь латиницей", () => {
  assert.equal(slugify("05 Карточка — обзор"), "05-kartochka-obzor");
  assert.equal(slugify("01б Вход, пароль показан"), "01b-vkhod-parol-pokazan");
  assert.equal(slugify("Компактный"), "kompaktnyy");
  assert.equal(
    slugify("Доска состояний графика настроения для разработки и проверки"),
    "doska-sostoyaniy-grafika-nastroeniya-dlya",
  );
});

test("каталог кадра зависит от страницы и доски", () => {
  assert.equal(
    frameDir({ page: "Врач", name: "Врач v4 · 02 Пациенты", parents: [] }),
    "doctor/02-patsienty",
  );
  assert.equal(
    frameDir({
      page: "Пациент",
      name: "Пациент · 06б Онбординг — Время, пасхалка 04:00",
      parents: [],
    }),
    "patient/06b-onbording-vremya-paskhalka-04-00",
  );
  assert.equal(
    frameDir({ page: "Врач", name: "Дневник · Доска состояний графика настроения", parents: [] }),
    "doctor/board-dnevnik-doska-sostoyaniy-grafika",
  );
  assert.equal(
    frameDir({ page: "Компоненты", name: "Раздел · Атомы", parents: ["Врач v4"] }),
    "components/doctor-atomy",
  );
  assert.equal(
    frameDir({
      page: "Компоненты",
      name: "Компактный",
      parents: ["Врач v4", "Раздел · Уровни шкалы"],
    }),
    "components/doctor-urovni-shkaly-kompaktnyy",
  );
  assert.equal(
    frameDir({ page: "Компоненты", name: "Раздел · Небо", parents: ["Пациент"] }),
    "components/patient-nebo",
  );
  assert.equal(
    frameDir({ page: "Компоненты", name: "Кнопка", parents: ["Компоненты"] }),
    "components/base-knopka",
  );
  assert.equal(frameDir({ page: "Компоненты", name: "Иконки", parents: [] }), "components/ikonki");
});

const box = (id: string, name: string, x: number, y: number, width: number, height: number) => ({
  id,
  name,
  x,
  y,
  width,
  height,
});

test("аннотация с кодом экрана привязывается к нему, остальные врача — к странице", () => {
  const screens = [
    box("1", "Врач v4 · 02 Пациенты", 1400, 120, 1280, 832),
    box("2", "Врач v4 · 06в Дневник — 3 месяца, по неделям", 9100, 9643, 1280, 1310),
  ];
  const annotations = [
    box("a", "Аннотация · Врач v4 · 02 Пациенты", 1400, 992, 1280, 400),
    box("b", "Аннотация · Пунктир предложений", 1500, 2000, 640, 420),
    box("c", "Аннотация · Врач v4 · 06в Дневник — 3 месяца", 9100, 10993, 1280, 451),
  ];

  assert.deepEqual(assignAnnotations("Врач", screens, annotations), {
    "1": ["a"],
    page: ["b"],
    "2": ["c"],
  });
});

test("аннотация пациента привязывается к ближайшему экрану", () => {
  const screens = [
    box("s17", "Пациент · 17 Отметка — опрос", 3600, 8125, 360, 3126),
    box("s18", "Пациент · 18 Отметка — готово", 4560, 8125, 360, 780),
  ];
  const annotations = [
    box("below", "Аннотация", 4560, 8945, 360, 244),
    box("right", "Аннотация · Прокрутка", 4000, 8125, 360, 224),
  ];

  assert.deepEqual(assignAnnotations("Пациент", screens, annotations), {
    s18: ["below"],
    s17: ["right"],
  });
});
