import { describe, expect, it } from "vitest";
import {
  defaultDisplayName,
  displayNameSuggestions,
  firstCheckInText,
  previewGreeting,
} from "./onboarding-flow";

describe("display name suggestions (P1-05)", () => {
  it("takes the first name from the full name by default", () => {
    expect(defaultDisplayName("Лебедева Марина Викторовна")).toBe("Марина");
    expect(defaultDisplayName("Марина Викторовна")).toBe("Марина");
    expect(defaultDisplayName("Марина")).toBe("Марина");
    expect(defaultDisplayName("  ")).toBe("");
  });

  it("offers the first name and the name with patronymic", () => {
    expect(displayNameSuggestions("Лебедева Марина Викторовна")).toEqual([
      "Марина",
      "Марина Викторовна",
    ]);
    expect(displayNameSuggestions("Марина Викторовна")).toEqual(["Марина", "Марина Викторовна"]);
    expect(displayNameSuggestions("Марина")).toEqual(["Марина"]);
    expect(displayNameSuggestions("")).toEqual([]);
  });

  it("previews the greeting of the current part of the day", () => {
    expect(previewGreeting("Марина", 20)).toBe("Добрый вечер, Марина");
    expect(previewGreeting("Марина", 9)).toBe("Доброе утро, Марина");
    expect(previewGreeting("Марина", 14)).toBe("Добрый день, Марина");
    expect(previewGreeting("", 14)).toBe("Добрый день");
  });
});

describe("done screen text (P1-04)", () => {
  it("says whether the first check-in is today or tomorrow", () => {
    expect(firstCheckInText("21:00", "19:30")).toBe("Первая отметка сегодня в 21:00.");
    expect(firstCheckInText("21:00", "21:00")).toBe("Первая отметка завтра в 21:00.");
    expect(firstCheckInText("09:00", "23:10")).toBe("Первая отметка завтра в 09:00.");
  });
});
