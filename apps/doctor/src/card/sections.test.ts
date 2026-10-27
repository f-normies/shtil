import { describe, expect, it, test } from "vitest";
import { SECTIONS, sectionOf, sectionsFor } from "./sections";

describe("sectionOf: выбранный раздел карточки по маршруту (спека #13, 10.9)", () => {
  it.each([
    ["/_doctor/patients/$patientId/", "/patients/$patientId"],
    ["/_doctor/patients/$patientId/scales", "/patients/$patientId/scales"],
    ["/_doctor/patients/$patientId/scales/$scaleId", "/patients/$patientId/scales"],
    ["/_doctor/patients/$patientId/therapy", "/patients/$patientId/therapy"],
    ["/_doctor/patients/$patientId/diary", "/patients/$patientId/diary"],
    ["/_doctor/patients/$patientId/adherence", "/patients/$patientId/adherence"],
    ["/_doctor/patients/$patientId/proposals", "/patients/$patientId/proposals"],
    ["/_doctor/patients/$patientId/documents", "/patients/$patientId"],
    [undefined, "/patients/$patientId"],
  ])("%s → %s", (routeId, section) => {
    expect(sectionOf(routeId)).toBe(section);
  });
});

describe("«Дневник» (спека #115)", () => {
  test("сегмент активен и ведёт на страницу дневника", () => {
    const diary = SECTIONS.find((s) => s.label === "Дневник");
    expect(diary).toEqual({ value: "/patients/$patientId/diary", label: "Дневник" });
  });
});

describe("разделы для заместителя (спека #56, раздел 6)", () => {
  test("заместителю — только «Обзор» и «Шкалы»", () => {
    expect(sectionsFor("deputy").map((s) => s.label)).toEqual(["Обзор", "Шкалы"]);
    expect(sectionsFor("lead")).toEqual(SECTIONS);
  });
});
