import { describe, expect, it } from "vitest";
import { canReadCachedPreferences } from "./preferences-access";

describe("cached patient preferences access", () => {
  const pointer = { id: "patient-a", role: "patient" as const, locked: false };

  it("allows only the confirmed unlocked owner when access has not been denied", () => {
    expect(canReadCachedPreferences(pointer, "patient-a", false)).toBe(true);
    expect(canReadCachedPreferences(pointer, "patient-b", false)).toBe(false);
    expect(canReadCachedPreferences({ ...pointer, locked: true }, "patient-a", false)).toBe(false);
    expect(canReadCachedPreferences(pointer, "patient-a", true)).toBe(false);
  });
});
