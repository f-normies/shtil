import { describe, expect, it } from "vitest";
import { MOOD_PALETTES, moodPaletteLabel } from "./onboarding-flow";

describe("mood palettes (P1-07)", () => {
  it("names the sets as in the design", () => {
    expect(moodPaletteLabel("warm")).toBe("Тёплый — хорошо");
    expect(moodPaletteLabel("green")).toBe("Зелёный — хорошо");
    expect(moodPaletteLabel("calm")).toBe("Спокойный, без оценки");
  });

  it("gives five colours per set in the order from «Очень тяжело» to «Хорошо»", () => {
    for (const palette of MOOD_PALETTES) {
      expect(palette.colors).toHaveLength(5);
    }
    expect(MOOD_PALETTES.find((p) => p.value === "warm")?.colors).toEqual([
      "var(--palette-brand-300)",
      "var(--palette-brand-200)",
      "var(--palette-mint-300)",
      "var(--palette-butter-200)",
      "var(--palette-peach-300)",
    ]);
  });
});
