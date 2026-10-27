import { expect, test } from "vitest";
import { CONSENT_REVOKED_EFFECT, consentRevokedLabel, consentRevokedShort } from "./consent";

test("revoked consent is marked with its date (спека #112, 3.7)", () => {
  // Полдень по UTC: дата одна и та же в любом часовом поясе браузера от −9 до +14.
  expect(consentRevokedLabel("2026-10-18T12:00:00Z")).toBe("Согласие отозвано 18.10.2026");
});

test("the list row shows the date without the year (кадр «Врач v4 · 02в»)", () => {
  expect(consentRevokedShort("2026-10-18T12:00:00Z")).toBe("Согласие отозвано 18.10");
});

test.each([[null], [undefined]])("consent in force has no mark: %s", (revokedAt) => {
  expect(consentRevokedLabel(revokedAt)).toBeNull();
  expect(consentRevokedShort(revokedAt)).toBeNull();
});

test("the card explains what the revoke means for data", () => {
  expect(CONSENT_REVOKED_EFFECT).toBe(
    "Новые данные от пациента не поступают, вход в кабинет закрыт. Собранные данные остаются в карте.",
  );
});
