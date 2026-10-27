import { describe, expect, it } from "vitest";
import { patientLoadMessage } from "./patientError";

describe("patientLoadMessage: чужой, несуществующий и некорректный id — «не найден» (D1-16)", () => {
  it.each([
    [{ status: 404, detail: "Patient not found" }, "пациент не найден."],
    [{ status: 422, detail: "Request validation failed", errors: [] }, "пациент не найден."],
    [{ status: 500 }, "не удалось загрузить карточку. Проверьте связь и обновите страницу."],
    [
      new TypeError("Failed to fetch"),
      "не удалось загрузить карточку. Проверьте связь и обновите страницу.",
    ],
  ])("%j → %s", (error, text) => {
    expect(patientLoadMessage(error)).toBe(text);
  });
});
