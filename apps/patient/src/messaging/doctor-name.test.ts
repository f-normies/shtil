import { expect, it } from "vitest";
import { doctorDisplayName, doctorInitials } from "./doctor-name";

it("addresses the doctor by first name and patronymic with surname-first initials", () => {
  expect(doctorDisplayName("Соколова Ирина Павловна")).toBe("Ирина Павловна");
  expect(doctorInitials("Соколова Ирина Павловна")).toBe("ИС");
});

it("falls back gracefully for short or empty names", () => {
  expect(doctorDisplayName("Иванов Пётр")).toBe("Пётр");
  expect(doctorInitials("Иванов Пётр")).toBe("ПИ");
  expect(doctorDisplayName("Иванов")).toBe("Иванов");
  expect(doctorInitials("Иванов")).toBe("И");
  expect(doctorDisplayName("")).toBe("Ваш врач");
  expect(doctorInitials("")).toBe("");
});
