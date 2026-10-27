import { describe, expect, it } from "vitest";
import {
  describeAppearance,
  pillDraftChanged,
  pillDraftFor,
  pillDraftReady,
} from "./pill-appearance";

const item = {
  appearance: null,
  appearance_origin: "instruction",
  appearance_revision: 2,
  default_appearance: { shape: "round", color: "white" },
} as const;

describe("pill appearance drafts (P1-08)", () => {
  it("starts from the instruction when the patient chose nothing", () => {
    expect(pillDraftFor(item)).toEqual({ shape: "round", color: "white" });
    expect(pillDraftFor({ ...item, appearance: { shape: "oval", color: "peach" } })).toEqual({
      shape: "oval",
      color: "peach",
    });
    expect(pillDraftFor({ ...item, default_appearance: null })).toEqual({
      shape: null,
      color: null,
    });
  });

  it("knows when the draft differs from what the server has", () => {
    expect(pillDraftChanged(item, { shape: "round", color: "white" })).toBe(false);
    expect(pillDraftChanged(item, { shape: "oval", color: "white" })).toBe(true);
    expect(pillDraftReady({ shape: "oval", color: null })).toBe(false);
    expect(pillDraftReady({ shape: "oval", color: "sand" })).toBe(true);
  });

  it("describes the look as colour then shape", () => {
    expect(describeAppearance({ shape: "round", color: "white" })).toBe("Белая круглая");
    expect(describeAppearance({ shape: "capsule", color: "peach" })).toBe("Персиковая капсула");
    expect(describeAppearance(null)).toBe("Вид не выбран");
  });
});
