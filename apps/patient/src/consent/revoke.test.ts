import { describe, expect, test } from "vitest";
import {
  CONSENT_REVOKED_TEXT,
  isConsentRevokedError,
  parseLoginSearch,
  revokeErrorText,
  unsentNotice,
} from "./revoke";

const REVOKED = {
  type: "about:blank",
  title: "Forbidden",
  status: 403,
  detail: "Consent revoked",
  code: "consent_revoked",
};

test("login explains a revoked consent in plain words (спека #112, 3.4)", () => {
  expect(CONSENT_REVOKED_TEXT).toBe(
    "Вы отозвали согласие на обработку данных. Вход закрыт. Чтобы вернуться, обратитесь к своему врачу.",
  );
});

describe("isConsentRevokedError", () => {
  test("403 with code consent_revoked", () => {
    expect(isConsentRevokedError(REVOKED)).toBe(true);
  });

  test.each([
    ["wrong password", { status: 401, title: "Unauthorized" }],
    ["403 without the code", { status: 403, title: "Forbidden" }],
    ["403 with another code", { ...REVOKED, code: "role_forbidden" }],
    ["the code on another status", { ...REVOKED, status: 401 }],
    ["rate limit", { status: 429, retry_after: 30 }],
    ["network failure", new TypeError("Failed to fetch")],
    ["empty body", ""],
    ["no error", null],
  ])("%s is a regular login error", (_name, error) => {
    expect(isConsentRevokedError(error)).toBe(false);
  });
});

describe("parseLoginSearch", () => {
  test("keeps the revoke reason after the revoke screen", () => {
    expect(parseLoginSearch({ reason: "consent_revoked" })).toEqual({ reason: "consent_revoked" });
  });

  test("keeps an uncertain revoke outcome without claiming consent was revoked", () => {
    expect(parseLoginSearch({ reason: "consent_revoke_unknown" })).toEqual({
      reason: "consent_revoke_unknown",
    });
  });

  test.each([[{}], [{ reason: "other" }], [{ reason: 1 }]])("drops anything else: %j", (search) => {
    expect(parseLoginSearch(search)).toEqual({});
  });
});

describe("revokeErrorText", () => {
  test("no connection: the answer may be lost after commit, so the state is not claimed", () => {
    expect(revokeErrorText(new TypeError("Failed to fetch"))).toBe(
      "Нет связи с сервером. Проверьте интернет и повторите.",
    );
  });

  test.each([
    [{ status: 500, title: "Internal Server Error" }],
    [{ status: 403 }],
    [{ status: 401 }],
    [null],
  ])("server refused or failed: %j", (error) => {
    expect(revokeErrorText(error)).toBe(
      "Не удалось узнать результат отзыва согласия. Проверьте связь и попробуйте ещё раз.",
    );
  });
});

describe("unsentNotice", () => {
  test("nothing unsent on this device: no warning", () => {
    expect(unsentNotice({ drafts: 0, outbox: 0 })).toBeNull();
  });

  test("drafts and queued answers are counted together", () => {
    expect(unsentNotice({ drafts: 1, outbox: 2 })).toBe(
      "Неотправленных ответов на этом устройстве: 3. Врач их не получит. После отзыва они удалятся с устройства.",
    );
  });

  test("count unknown: warn without a number", () => {
    expect(unsentNotice(null)).toBe(
      "Неотправленные ответы на этом устройстве врач не получит. После отзыва они удалятся с устройства.",
    );
  });
});
