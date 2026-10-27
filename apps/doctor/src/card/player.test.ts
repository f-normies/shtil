/**
 * «Врач v4/Плеер» дневника (спека #115, раздел 3.5; аннотация кадра 06): перемотка с
 * клавиатуры и что считать сбоем воспроизведения.
 */
import { describe, expect, test } from "vitest";
import { isPlayFailure, seekTarget } from "./player";

describe("seekTarget", () => {
  test.each([
    ["ArrowRight", 10, 15],
    ["ArrowLeft", 10, 5],
    ["ArrowLeft", 3, 0],
    ["ArrowRight", 40, 42],
    ["Home", 30, 0],
    ["End", 3, 42],
  ])("%s from %d s of 42 s goes to %d s", (key, position, expected) => {
    expect(seekTarget(key, position, 42)).toBe(expected);
  });

  test("other keys do not seek", () => {
    expect(seekTarget("Enter", 10, 42)).toBeNull();
    expect(seekTarget(" ", 10, 42)).toBeNull();
  });
});

describe("isPlayFailure", () => {
  test("a play interrupted by a pause is not a failure: another record started", () => {
    expect(isPlayFailure(new DOMException("interrupted", "AbortError"))).toBe(false);
  });

  test.each(["NotSupportedError", "NotAllowedError"])(
    "%s is a failure to load the record",
    (name) => {
      expect(isPlayFailure(new DOMException("no", name))).toBe(true);
    },
  );
});
