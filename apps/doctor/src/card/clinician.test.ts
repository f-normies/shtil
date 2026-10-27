import type { Schemas } from "@compass/api-client";
import { describe, expect, test } from "vitest";
import { clinicianTotal } from "./clinician";

type Scale = Schemas["Scale"];

const ref = { source: "kr-depression-2024", locator: "прил. Г1, ключ" };

/** Сокращённая HAMD-17: три пункта и ключ из `knowledge/scales/hamd-17.yaml`. */
const HAMD: Scale = {
  id: "hamd-17",
  version: 1,
  title: "HAMD-17",
  instruction: "",
  purpose: "monitoring",
  respondent: "clinician",
  status: "reviewed",
  license: { terms: "", allowed: true, source: ref },
  validation: [ref],
  items: ["q1", "q2", "q3"].map((id) => ({
    id,
    text: id,
    options: [0, 1, 2, 3, 4].map((value) => ({ value, text: String(value) })),
  })),
  scores: [
    {
      id: "total",
      items: ["q1", "q2", "q3"],
      bands: [
        { min: 0, max: 7, code: "normal", label: "Норма", source: ref },
        { min: 8, max: 13, code: "mild", label: "Лёгкое депрессивное расстройство", source: ref },
      ],
    },
  ],
};

describe("clinicianTotal", () => {
  // Источник ключа — КР «Депрессивный эпизод, РДР», прил. Г1 (hamd-17.yaml).
  test.each([
    [{}, { answered: 0, total: 3, sum: null, max: 12, band: null }],
    [{ q1: 2 }, { answered: 1, total: 3, sum: null, max: 12, band: null }],
    [
      { q1: 2, q2: 2, q3: 3 },
      { answered: 3, total: 3, sum: 7, max: 12, band: "Норма" },
    ],
    [
      { q1: 4, q2: 2, q3: 2 },
      { answered: 3, total: 3, sum: 8, max: 12, band: "Лёгкое депрессивное расстройство" },
    ],
  ])("%o → %o", (answers, expected) => {
    expect(clinicianTotal(HAMD, answers)).toEqual(expected);
  });

  test("a sum outside every band has no band word", () => {
    const [score] = HAMD.scores;
    if (!score) throw new Error("no score");
    const short = { ...HAMD, scores: [{ ...score, bands: score.bands.slice(0, 1) }] };
    expect(clinicianTotal(short, { q1: 4, q2: 4, q3: 4 }).band).toBeNull();
  });
});
