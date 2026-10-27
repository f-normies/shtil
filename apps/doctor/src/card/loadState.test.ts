import { describe, expect, it } from "vitest";
import { showsFailure } from "./loadState";

describe("showsFailure: ошибка загрузки только без данных (кадр 12, D1-02)", () => {
  it.each([
    [{ isError: true, data: undefined }, true],
    [{ isError: true, data: [] }, false],
    [{ isError: true, data: [{ id: "a" }] }, false],
    [{ isError: false, data: undefined }, false],
  ])("%j → %s", (query, failed) => {
    expect(showsFailure(query)).toBe(failed);
  });
});
