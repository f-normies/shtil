import { expect, it } from "vitest";
import { classifyDiaryResponse } from "./queue-policy";

it.each([
  [200, undefined, "confirmed"],
  [201, undefined, "confirmed"],
  [null, undefined, "retry"],
  [503, undefined, "retry"],
  [429, undefined, "retry"],
  [401, undefined, "reauthenticate"],
  [403, "patient_mismatch", "owner-mismatch"],
  [409, "revision_conflict", "conflict"],
  [422, "invalid_entry", "blocked"],
] as const)("classifies status %s and code %s as %s", (status, code, expected) => {
  expect(classifyDiaryResponse(status, code)).toEqual({ kind: expected });
});
