import { describe, expect, test } from "vitest";
import {
  confirmBody,
  confirmError,
  editHint,
  editItems,
  removeItem,
  retryError,
  updateItem,
} from "./summary";

/** Лист правки сводки и ответы на ошибки подтверждения и повтора (спека #38, 3.5 и 3.6). */

describe("edit sheet", () => {
  const statements = [
    { text: "Тревога снизилась", facts: [1] },
    { text: "Открыт сигнал «Обязательно»", facts: [2] },
    { text: "Жалобы на сон", facts: [3, 4] },
  ];

  test("unchanged draft is sent as is", () => {
    expect(confirmBody(editItems(statements))).toEqual({ statements });
  });

  test("edited text keeps the fact numbers of the statement", () => {
    const items = editItems(statements);
    const edited = updateItem(items, items[2]?.key ?? -1, "  Плохо спит третью ночь ");
    expect(confirmBody(edited).statements[2]).toEqual({
      text: "Плохо спит третью ночь",
      facts: [3, 4],
    });
  });

  test("a removed statement is not sent; the others keep their numbers", () => {
    const items = editItems(statements);
    expect(confirmBody(removeItem(items, items[1]?.key ?? -1))).toEqual({
      statements: [
        { text: "Тревога снизилась", facts: [1] },
        { text: "Жалобы на сон", facts: [3, 4] },
      ],
    });
  });

  test("editing does not change the original items", () => {
    const items = editItems(statements);
    updateItem(items, items[0]?.key ?? -1, "другое");
    removeItem(items, items[0]?.key ?? -1);
    expect(confirmBody(items)).toEqual({ statements });
  });

  test("empty text blocks confirmation", () => {
    const items = editItems(statements);
    expect(editHint(updateItem(items, items[0]?.key ?? -1, "   "))).toBe(
      "Заполните текст каждого утверждения или удалите его",
    );
  });

  test("at least one statement stays", () => {
    const items = editItems(statements.slice(0, 1));
    expect(editHint(removeItem(items, items[0]?.key ?? -1))).toBe(
      "Оставьте хотя бы одно утверждение",
    );
  });

  test("valid edit has no hint", () => {
    expect(editHint(editItems(statements))).toBeNull();
  });
});

describe("confirmError: 404, 409 and 422 from confirm", () => {
  test("stale draft: tell the doctor the edit must be repeated and fetch the new one", () => {
    expect(confirmError({ status: 409, code: "stale", detail: "x" })).toEqual({
      text: "Данные пациента изменились. Сводка обновляется, правку нужно будет повторить.",
      refetch: true,
    });
  });

  test("already confirmed: just fetch the confirmed version", () => {
    expect(confirmError({ status: 409, code: "already_confirmed" })).toEqual({
      text: null,
      refetch: true,
    });
  });

  test("fact number unknown to the snapshot: ask to refresh and fetch again", () => {
    expect(confirmError({ status: 422, code: "unknown_fact" })).toEqual({
      text: "Не удалось подтвердить сводку, обновите её",
      refetch: true,
    });
  });

  // Сервер не принимает утверждений больше, чем в черновике, и фактов не из черновика:
  // любой 422 и незнакомый 409 — общий текст и новый запрос.
  test.each([
    [{ status: 422, code: "too_many_statements" }],
    [{ status: 422, title: "Unprocessable Entity" }],
    [{ status: 409, code: "something_new" }],
  ])("%j: ask to refresh and fetch again", (error) => {
    expect(confirmError(error)).toEqual({
      text: "Не удалось подтвердить сводку, обновите её",
      refetch: true,
    });
  });

  test("summary not found: just fetch the current one", () => {
    expect(confirmError({ status: 404, title: "Not Found" })).toEqual({
      text: null,
      refetch: true,
    });
  });

  test("any other failure keeps the edit and asks to retry", () => {
    expect(confirmError(new TypeError("Failed to fetch"))).toEqual({
      text: "Сводка не подтверждена. Проверьте связь и попробуйте ещё раз.",
      refetch: false,
    });
  });
});

describe("retryError", () => {
  test("calculation is not failed any more: fetch the summary without an error", () => {
    expect(retryError({ status: 409, code: "not_failed" })).toEqual({ text: null, refetch: true });
  });

  test("other failures ask to try again", () => {
    expect(retryError(new TypeError("Failed to fetch"))).toEqual({
      text: "Не удалось отправить запрос. Попробуйте ещё раз.",
      refetch: false,
    });
  });
});
