import { describe, expect, it } from "vitest";
import { CONSENT_SUMMARY, formatConsentVersion } from "./registration";

describe("registration consent (P1-02)", () => {
  it("shows a dated version as a date and other versions verbatim", () => {
    expect(formatConsentVersion("2026-10-16-draft")).toBe("Редакция от 16.10.2026");
    expect(formatConsentVersion("v1")).toBe("Версия v1");
  });

  it("summarises the consent in four points", () => {
    expect(CONSENT_SUMMARY.map((point) => point.title)).toEqual([
      "Что собираем",
      "Кто видит",
      "Уведомления",
      "Как отозвать",
    ]);
  });
});
