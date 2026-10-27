import type { Schemas } from "@compass/api-client";
import { describe, expect, it } from "vitest";
import { evidenceText } from "./evidence";

const PHQ9 = {
  scale: {
    id: "phq-9",
    title: "Опросник здоровья пациента (PHQ-9)",
    items: [
      { id: "phq9-1", text: "Интерес", options: [] },
      {
        id: "phq9-9",
        text: "Мысли о смерти",
        options: [
          { value: 0, text: "Совсем нет" },
          { value: 1, text: "Несколько дней" },
        ],
      },
    ],
  },
} as unknown as Schemas["ScaleDefinitionOut"];

const AT = "2026-10-19T06:12:00Z";

describe("evidenceText: данные сигнала словами (кадр «Врач v4 · 05»)", () => {
  it("ответ на пункт: номер пункта, вариант ответа и балл", () => {
    expect(
      evidenceText({ scale: "phq-9", key: "phq9-9", value: 1, isScore: false }, PHQ9, AT),
    ).toMatch(/^PHQ-9, пункт 2: ответ «Несколько дней», 1 балл\. Сигнал \d\d\.10, \d\d:12\.$/);
  });

  it("сумма шкалы — баллы", () => {
    expect(
      evidenceText({ scale: "phq-9", key: "total", value: 16, isScore: true }, PHQ9, AT),
    ).toMatch(/^PHQ-9: 16 баллов\. Сигнал /);
  });

  it("без определения шкалы — коды как есть", () => {
    expect(
      evidenceText({ scale: "phq-9", key: "phq9-9", value: 2, isScore: false }, undefined, null),
    ).toBe("phq-9, phq9-9: 2 балла.");
  });
});
