import { describe, expect, test } from "vitest";
import { canRetryBlockedSubmission, classifySubmissionResponse } from "./queue-policy";

describe("submission response policy", () => {
  test.each([
    ["201 confirms and removes the queued answer", 201, undefined, { kind: "confirmed" }],
    ["200 idempotent replay confirms the same answer", 200, undefined, { kind: "confirmed" }],
    ["network failure keeps the answer retryable", null, undefined, { kind: "retry" }],
    ["request timeout keeps the answer retryable", 408, undefined, { kind: "retry" }],
    ["rate limiting keeps the answer retryable", 429, undefined, { kind: "retry" }],
    ["server failure keeps the answer retryable", 503, undefined, { kind: "retry" }],
    ["401 pauses the owner queue for reauthentication", 401, undefined, { kind: "reauthenticate" }],
    [
      "403 stops the queue when the session owner differs",
      403,
      "patient_mismatch",
      { kind: "owner-mismatch" },
    ],
    [
      "404 blocks only the unavailable-scale answer",
      404,
      "scale_unavailable",
      { kind: "blocked", error: { status: 404, code: "scale_unavailable" } },
    ],
    [
      "409 preserves an answer made from an old definition",
      409,
      "scale_version_changed",
      { kind: "blocked", error: { status: 409, code: "scale_version_changed" } },
    ],
    [
      "409 submission conflicts keep the original key",
      409,
      "submission_conflict",
      { kind: "blocked", error: { status: 409, code: "submission_conflict" } },
    ],
    [
      "422 leaves invalid answers available for correction",
      422,
      "validation_error",
      { kind: "blocked", error: { status: 422, code: "validation_error" } },
    ],
    [
      "an unrecognized client error is isolated to one answer",
      400,
      undefined,
      {
        kind: "blocked",
        error: { status: 400, code: "http_400" },
      },
    ],
    ["an unexpected 204 does not claim the API saved an answer", 204, undefined, { kind: "retry" }],
  ] as const)("%s", (_case, status, code, expected) => {
    expect(classifySubmissionResponse(status, code)).toEqual(expected);
  });
});

describe("manual retry of a blocked submission", () => {
  test.each([
    ["404 scale_unavailable is temporary", { status: 404, code: "scale_unavailable" }, true],
    ["409 lead_doctor_missing is temporary", { status: 409, code: "lead_doctor_missing" }, true],
    [
      "409 scale_version_changed needs the new form",
      { status: 409, code: "scale_version_changed" },
      false,
    ],
    [
      "409 submission_conflict keeps the key without resending",
      { status: 409, code: "submission_conflict" },
      false,
    ],
    ["422 needs corrected answers", { status: 422, code: "validation_error" }, false],
    ["an unknown client error is not retried", { status: 400, code: "http_400" }, false],
    ["a record without an error is not blocked", null, false],
  ] as const)("%s", (_case, error, expected) => {
    expect(canRetryBlockedSubmission(error)).toBe(expected);
  });
});
