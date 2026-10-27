import { describe, expect, it } from "vitest";
import { nextIndex } from "./listNav";

describe("nextIndex: ↑↓, Home и End в списке с одной остановкой Tab (кадры 02 и 03)", () => {
  it.each([
    ["ArrowDown", 0, 3, 1],
    ["ArrowDown", 2, 3, 2],
    ["ArrowDown", -1, 3, 0],
    ["ArrowUp", 2, 3, 1],
    ["ArrowUp", 0, 3, 0],
    ["ArrowUp", -1, 3, 0],
    ["Home", 2, 3, 0],
    ["End", 0, 3, 2],
    ["Enter", 1, 3, null],
    ["ArrowDown", 0, 0, null],
  ])("%s с позиции %i из %i → %s", (key, at, length, next) => {
    expect(nextIndex(key, at, length)).toBe(next);
  });
});
