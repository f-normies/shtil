import { describe, expect, it } from "vitest";
import { loginErrorMessage, loginFieldErrors, retryAfterSeconds, retryAfterText } from "./problem";

describe("retryAfterText", () => {
  it.each([
    // Занятая блокировка при двойной отправке формы — 1 секунда.
    [{ status: 429, code: "rate_limited", retry_after: 1 }, "через несколько секунд"],
    [{ status: 429, code: "rate_limited", retry_after: 59 }, "через несколько секунд"],
    [{ status: 429, code: "rate_limited", retry_after: 60 }, "через 1 мин."],
    [{ status: 429, code: "rate_limited", retry_after: 61 }, "через 2 мин."],
    [{ status: 429, code: "rate_limited", retry_after: 3600 }, "через 60 мин."],
    // Лимит по адресу — до суток.
    [{ status: 429, code: "rate_limited", retry_after: 3601 }, "через 2 ч."],
    [{ status: 429, code: "rate_limited", retry_after: 86400 }, "через 24 ч."],
    [{ status: 429 }, null],
    [{ status: 401, retry_after: 60 }, null],
    [null, null],
    ["429", null],
  ])("%j → %s", (error, text) => {
    expect(retryAfterText(error)).toBe(text);
  });
});

describe("loginErrorMessage", () => {
  it.each([
    [
      { status: 429, code: "rate_limited", retry_after: 900 },
      { field: "password", text: "Слишком много попыток входа. Повторите через 15 мин." },
    ],
    [
      { status: 429, code: "rate_limited", retry_after: 5 },
      { field: "password", text: "Слишком много попыток входа. Повторите через несколько секунд." },
    ],
    [
      { status: 401 },
      { field: "password", text: "Почта или пароль не подошли. Проверьте и попробуйте ещё раз." },
    ],
    [{ status: 422 }, { field: "email", text: "Введите адрес почты, например name@example.ru." }],
    [{ status: 403 }, { field: "password", text: "Вход закрыт." }],
    [
      new TypeError("fetch failed"),
      { field: "password", text: "Не удалось войти. Проверьте связь и повторите." },
    ],
    [
      { status: 503 },
      { field: "password", text: "Не удалось войти. Проверьте связь и повторите." },
    ],
  ])("%j → %j", (error, message) => {
    expect(loginErrorMessage(error)).toEqual(message);
  });
});

describe("retryAfterSeconds", () => {
  it.each([
    [{ status: 429, retry_after: 180 }, 180],
    [{ status: 429, retry_after: 0 }, null],
    [{ status: 429 }, null],
    [{ status: 401, retry_after: 60 }, null],
    [null, null],
  ])("%j → %s", (error, seconds) => {
    expect(retryAfterSeconds(error)).toBe(seconds);
  });
});

describe("loginFieldErrors: пустые поля не уходят на сервер (кадр 01)", () => {
  it.each([
    [
      { email: "", password: "" },
      { email: "Введите почту.", password: "Введите пароль." },
    ],
    [{ email: "  ", password: "x" }, { email: "Введите почту." }],
    [{ email: "a@b.ru", password: "" }, { password: "Введите пароль." }],
    [{ email: "a@b.ru", password: "x" }, {}],
  ])("%j → %j", (fields, errors) => {
    expect(loginFieldErrors(fields)).toEqual(errors);
  });
});
