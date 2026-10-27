import { describe, expect, it } from "vitest";
import { parseOnboardingSearch, resolveOnboardingStep, segmentClassName } from "./onboarding-flow";

describe("onboarding step in the address (P1-13)", () => {
  it("keeps a known step and drops anything else", () => {
    expect(parseOnboardingSearch({ step: "mood" })).toEqual({ step: "mood" });
    expect(parseOnboardingSearch({ step: "unknown" })).toEqual({});
    expect(parseOnboardingSearch({})).toEqual({});
  });

  it("falls back to the welcome step when the address names a missing step", () => {
    const steps = ["welcome", "name", "reminder", "done"] as const;
    expect(resolveOnboardingStep("reminder", [...steps])).toBe("reminder");
    expect(resolveOnboardingStep("pills", [...steps])).toBe("welcome");
    expect(resolveOnboardingStep(undefined, [...steps])).toBe("welcome");
  });

  it("paints unfinished segments in a colour visible on the sky scene", () => {
    expect(segmentClassName(0, 1)).toContain("bg-primary");
    expect(segmentClassName(1, 1)).toContain("bg-accent-sky");
    expect(segmentClassName(1, 1)).not.toContain("bg-accent-calm-soft");
  });
});
