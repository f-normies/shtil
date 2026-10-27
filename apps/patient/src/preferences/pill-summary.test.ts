import { describe, expect, it } from "vitest";
import { pillAppearanceSummary } from "./pill-summary";

const item = (
  trade_name: string,
  appearance: {
    color: "white" | "sand" | "peach" | "blue";
    shape: "round" | "oval" | "capsule";
  } | null,
) => ({ trade_name, appearance });

describe("pillAppearanceSummary", () => {
  it("names the drug with its colour and shape", () => {
    expect(pillAppearanceSummary([item("Авиандр", { color: "white", shape: "round" })])).toBe(
      "Авиандр: белая, круглая",
    );
  });

  it("lists several prescriptions and skips those without a chosen look", () => {
    expect(
      pillAppearanceSummary([
        item("Авиандр", { color: "sand", shape: "capsule" }),
        item("Сертралин", null),
        item("Флувоксамин", { color: "blue", shape: "oval" }),
      ]),
    ).toBe("Авиандр: песочная, капсула; Флувоксамин: голубая, овальная");
  });

  it("says nothing is chosen when no prescription has a look", () => {
    expect(pillAppearanceSummary([item("Авиандр", null)])).toBe("Не выбран");
    expect(pillAppearanceSummary([])).toBe("Не выбран");
  });
});
