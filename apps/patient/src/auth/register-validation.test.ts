import { describe, expect, it } from "vitest";
import { FULL_NAME_MAX_LENGTH, registerFieldErrors } from "./register-validation";

const valid = {
  fullName: "Версиева Мария Ивановна",
  email: "maria@example.ru",
  password: "12345678",
};

describe("registerFieldErrors", () => {
  it("accepts a filled form", () => {
    expect(registerFieldErrors(valid)).toEqual({});
  });

  it("asks for the full name when it is blank", () => {
    expect(registerFieldErrors({ ...valid, fullName: "   " })).toEqual({
      fullName: "Укажите фамилию, имя и отчество, как в направлении врача.",
    });
  });

  it("limits the full name to the server maximum", () => {
    expect(
      registerFieldErrors({ ...valid, fullName: "а".repeat(FULL_NAME_MAX_LENGTH + 1) }),
    ).toEqual({ fullName: `ФИО не длиннее ${FULL_NAME_MAX_LENGTH} символов.` });
    expect(registerFieldErrors({ ...valid, fullName: "а".repeat(FULL_NAME_MAX_LENGTH) })).toEqual(
      {},
    );
  });

  it("checks the email and the password at the same time", () => {
    expect(registerFieldErrors({ fullName: "", email: "maria", password: "123" })).toEqual({
      fullName: "Укажите фамилию, имя и отчество, как в направлении врача.",
      email: "Введите адрес почты, например name@example.ru.",
      password: "Пароль не короче 8 символов.",
    });
  });
});
