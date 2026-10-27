import { describe, expect, it } from "vitest";
import {
  changedPreferences,
  getOnboardingSteps,
  moodAttribute,
  type OnboardingStepId,
  showNightMoon,
  skipOnboardingStep,
} from "./onboarding-flow";

const initialValues = {
  display_name: "Марина Викторовна",
  reminder_time: "21:00",
  mood_palette: "warm",
  story_mode: "voice",
  text_size: "normal",
} as const;

const steps: OnboardingStepId[] = [
  "welcome",
  "name",
  "reminder",
  "mood",
  "story",
  "pills",
  "text",
  "done",
];

describe("skipOnboardingStep", () => {
  it("completes a first run from welcome without replacing server defaults", () => {
    const draftValues = { ...initialValues, display_name: "Марина" };

    expect(
      skipOnboardingStep({
        currentStep: "welcome",
        steps,
        initialValues,
        draftValues,
        onboardingCompleted: false,
      }),
    ).toEqual({ kind: "complete", values: initialValues });
  });

  it("skips only the current setting and preserves earlier choices", () => {
    const draftValues = {
      ...initialValues,
      display_name: "Марина",
      reminder_time: "22:15",
    };

    expect(
      skipOnboardingStep({
        currentStep: "reminder",
        steps,
        initialValues,
        draftValues,
        onboardingCompleted: false,
      }),
    ).toEqual({
      kind: "advance",
      step: "mood",
      values: { ...draftValues, reminder_time: initialValues.reminder_time },
    });
  });
});

describe("onboarding configuration", () => {
  it("omits the pill step when the patient has no active prescription", () => {
    expect(getOnboardingSteps(false)).not.toContain("pills");
    expect(getOnboardingSteps(false)).toHaveLength(7);
    expect(getOnboardingSteps(true)).toContain("pills");
  });

  it("sends only values that changed from the server snapshot", () => {
    expect(
      changedPreferences(initialValues, {
        ...initialValues,
        display_name: "Марина",
        reminder_time: "21:00",
      }),
    ).toEqual({ display_name: "Марина" });
  });

  it("maps preference palettes to existing patient theme attributes", () => {
    expect(moodAttribute("warm")).toBe("teplyy");
    expect(moodAttribute("green")).toBe("zelenyy");
    expect(moodAttribute("calm")).toBe("spokoynyy");
  });

  it.each([
    // Кадры 06б и 28: луна с 00:00 до 04:59 в онбординге и в профиле.
    ["00:00", true],
    ["03:45", true],
    ["04:00", true],
    ["04:15", true],
    ["04:45", true],
    ["05:00", false],
    ["21:00", false],
    ["23:45", false],
    ["", false],
  ])("shows the night moon for %s → %s", (time, visible) => {
    expect(showNightMoon(time)).toBe(visible);
  });
});
