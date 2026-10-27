import { describe, expect, it } from "vitest";
import { consentVersionLabel, describeAcceptedConsent, formatConsentDate } from "./consent-label";

describe("consentVersionLabel", () => {
  it.each([
    ["2026-10-16-draft", "от 16.10.2026"],
    ["2026-10-16", "от 16.10.2026"],
    ["1", "1"],
    ["v2", "v2"],
  ])("%s → %s", (version, label) => {
    expect(consentVersionLabel(version)).toBe(label);
  });
});

describe("formatConsentDate", () => {
  it("prints the Moscow calendar date without a trailing period", () => {
    expect(formatConsentDate("2026-10-27T10:15:00Z")).toBe("27.10.2026");
    // 23:30 UTC — уже следующий день по Москве.
    expect(formatConsentDate("2026-10-26T23:30:00Z")).toBe("27.10.2026");
  });
});

describe("describeAcceptedConsent", () => {
  it("joins the version and the acceptance date as in the Figma chip", () => {
    expect(
      describeAcceptedConsent({ version: "2026-10-16-draft", given_at: "2026-10-27T10:15:00Z" }),
    ).toBe("Версия от 16.10.2026, принята 27.10.2026");
    expect(describeAcceptedConsent({ version: "1", given_at: "2026-10-15T10:15:00Z" })).toBe(
      "Версия 1, принята 15.10.2026",
    );
  });
});
