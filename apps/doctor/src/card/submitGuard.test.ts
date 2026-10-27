import { describe, expect, it } from "vitest";
import { createSubmitGuard } from "./submitGuard";

describe("createSubmitGuard: второй щелчок до ответа запрос не дублирует (D1-15)", () => {
  it("пропускает первый вызов, держит следующие до release", () => {
    const guard = createSubmitGuard();
    expect(guard.take()).toBe(true);
    expect(guard.take()).toBe(false);
    guard.release();
    expect(guard.take()).toBe(true);
  });
});
