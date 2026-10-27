import type { Schemas } from "@compass/api-client";
import { describe, expect, it } from "vitest";
import { aboutLine } from "./about";
import { patient } from "./fixtures";

const diagnosis = (icd10: string, resolved = false) =>
  ({ icd10, resolved_at: resolved ? "2026-10-01T00:00:00Z" : null }) as Schemas["DiagnosisOut"];
const prescription = (status: string) =>
  ({ status, trade_name: "Авиандр", dose_mg: 20 }) as Schemas["PrescriptionOut"];

describe("aboutLine: сведения в шапке через запятую (спека #13, 10.3)", () => {
  it("возраст, действующие диагнозы, действующее назначение и начало наблюдения", () => {
    const text = aboutLine({
      patient: patient({ birth_date: "1992-03-01", created_at: "2026-09-22T08:00:00" }),
      diagnoses: [diagnosis("F41.1"), diagnosis("G47.0", true), diagnosis("F32.0")],
      prescriptions: [prescription("stopped"), prescription("active")],
      today: "2026-10-19",
    });
    expect(text).toBe("34 года, F41.1, F32.0, Авиандр 20 мг, наблюдение с 22.09");
  });

  it("без даты рождения, диагнозов и назначений — только начало наблюдения", () => {
    const text = aboutLine({
      patient: patient({ created_at: "2026-09-22T08:00:00" }),
      diagnoses: [],
      prescriptions: [],
      today: "2026-10-19",
    });
    expect(text).toBe("наблюдение с 22.09");
  });

  it("год начала наблюдения показан, если он не текущий", () => {
    const text = aboutLine({
      patient: patient({ created_at: "2025-12-30T08:00:00" }),
      diagnoses: [],
      prescriptions: [],
      today: "2026-10-19",
    });
    expect(text).toBe("наблюдение с 30.12.2025");
  });
});

describe("aboutLine: одинаковые коды диагнозов схлопываются (#191, D1-12)", () => {
  it.each([
    [["F32.1", "F41.1", "F41.1", "F41.1"], "F32.1, F41.1"],
    [["F41.1", "F32.1", "F41.1"], "F41.1, F32.1"],
    [["F41.1"], "F41.1"],
  ])("%j → %s", (codes, expected) => {
    const text = aboutLine({
      patient: patient({ created_at: "2026-09-22T08:00:00" }),
      diagnoses: codes.map((c) => diagnosis(c)),
      prescriptions: [],
      today: "2026-10-19",
    });
    expect(text).toBe(`${expected}, наблюдение с 22.09`);
  });
});
