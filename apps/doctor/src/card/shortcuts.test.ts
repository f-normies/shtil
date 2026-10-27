import { describe, expect, it } from "vitest";
import { isFindShortcut } from "./shortcuts";

describe("isFindShortcut: ⌘F и Ctrl+F ведут в поиск по ФИО (кадры 02 и 03)", () => {
  it.each([
    [{ key: "f", metaKey: true, ctrlKey: false, altKey: false, shiftKey: false }, true],
    [{ key: "F", metaKey: false, ctrlKey: true, altKey: false, shiftKey: false }, true],
    [
      { key: "а", code: "KeyF", metaKey: true, ctrlKey: false, altKey: false, shiftKey: false },
      true,
    ],
    [{ key: "f", metaKey: false, ctrlKey: false, altKey: false, shiftKey: false }, false],
    [{ key: "f", metaKey: true, ctrlKey: false, altKey: false, shiftKey: true }, false],
    [{ key: "g", metaKey: true, ctrlKey: false, altKey: false, shiftKey: false }, false],
  ])("%j → %s", (event, found) => {
    expect(isFindShortcut(event)).toBe(found);
  });
});
