import { describe, expect, test } from "vitest";
import {
  documentMeta,
  finishedDocuments,
  plural,
  pollInterval,
  retryErrorText,
  selectedDocument,
  statusView,
  uploadErrorText,
  whenText,
} from "./documents";
import { document } from "./fixtures";

// Время — местное: так тексты «сегодня, 10:42» не зависят от часового пояса машины.
const NOW = new Date(2026, 9, 22, 12, 0);
const at = (hour: number, minute: number, day = 22, month = 9, year = 2026) =>
  new Date(year, month, day, hour, minute).toISOString();

describe("statusView", () => {
  test.each([
    ["parsing", "Распознаётся", "work"],
    ["extracting", "Извлекаются данные", "work"],
    ["done", "Готово", "done"],
  ] as const)("%s reads as «%s»", (status, text, tone) => {
    const view = statusView(
      document({ status, created_at: at(11, 55), parsed_at: at(11, 57) }),
      NOW,
    );

    expect(view).toEqual({ text, tone, long: false, retry: false });
  });

  // Причины — коды ошибок раздела 3 спеки #30.
  test.each([
    ["page_unreadable", 3, "Не удалось: страница 3 не читается"],
    ["page_unreadable", null, "Не удалось: страница не читается"],
    ["empty_text", null, "Не удалось: в документе не найден текст"],
    ["text_too_long", null, "Не удалось: текст длиннее 15 000 знаков"],
    ["parse_failed", null, "Не удалось: текст не распознан"],
    ["extract_failed", null, "Не удалось: данные не извлечены"],
  ] as const)("failed with %s and page %s offers a retry", (code, page, text) => {
    const view = statusView(
      document({ status: "failed", error_code: code, error_page: page }),
      NOW,
    );

    expect(view).toEqual({ text, tone: "failed", long: false, retry: true });
  });

  test("parsing longer than 10 minutes is a long status", () => {
    const view = statusView(document({ status: "parsing", created_at: at(11, 49) }), NOW);

    expect(view.long).toBe(true);
  });

  test("extraction is timed from the end of parsing", () => {
    const doc = document({ status: "extracting", created_at: at(9, 0), parsed_at: at(11, 55) });

    expect(statusView(doc, NOW).long).toBe(false);
  });
});

describe("pollInterval", () => {
  test.each([
    [[], false],
    [
      [document({ status: "done" }), document({ status: "failed", error_code: "parse_failed" })],
      false,
    ],
    [[document({ status: "done" }), document({ status: "parsing" })], 5000],
    [[document({ status: "extracting" })], 5000],
  ] as const)("polls every 5 s only while a document is in work (%#)", (docs, interval) => {
    expect(pollInterval([...docs])).toBe(interval);
  });

  test("does not poll before the first answer", () => {
    expect(pollInterval(undefined)).toBe(false);
  });
});

describe("finishedDocuments", () => {
  test("a document that became done means new proposals", () => {
    const before = [document({ status: "extracting" })];
    const after = [document({ status: "done" })];

    expect(finishedDocuments(before, after)).toBe(true);
  });

  test.each([
    ["first load", undefined, [document({ status: "done" })]],
    ["still working", [document({ status: "parsing" })], [document({ status: "extracting" })]],
    ["already done", [document({ status: "done" })], [document({ status: "done" })]],
    [
      "failed",
      [document({ status: "extracting" })],
      [document({ status: "failed", error_code: "extract_failed" })],
    ],
  ])("%s does not", (_, before, after) => {
    expect(finishedDocuments(before, after)).toBe(false);
  });
});

describe("documentMeta", () => {
  test("a parsed PDF shows document date, upload time, kind and pages", () => {
    const doc = document({ created_at: at(10, 42), document_date: "2026-09-12", pages: 3 });

    expect(documentMeta(doc, NOW)).toBe(
      "Документ от 12.09.2026 · загружен сегодня, 10:42 · PDF, 3 страницы",
    );
  });

  test("a recognised image warns that names could be distorted", () => {
    const doc = document({
      content_type: "image/jpeg",
      pages: 1,
      ocr: true,
      created_at: at(9, 0, 14),
      document_date: "2025-06-03",
    });

    expect(documentMeta(doc, NOW)).toBe(
      "Документ от 03.06.2025 · загружен 14.10 · JPEG, 1 страница. " +
        "Текст распознан с изображения: названия могли исказиться",
    );
  });

  test("a parsed document without a date says so", () => {
    const doc = document({ document_date: null, created_at: at(9, 0, 3, 0, 2025), pages: 5 });

    expect(documentMeta(doc, NOW)).toBe(
      "Дата документа не указана · загружен 03.01.2025 · PDF, 5 страниц",
    );
  });

  test("a document in work does not guess its date yet", () => {
    const doc = document({ status: "parsing", document_date: null, created_at: at(11, 58) });

    expect(documentMeta(doc, NOW)).toBe("Загружен сегодня, 11:58 · PDF, 3 страницы");
  });
});

describe("whenText", () => {
  test.each([
    [at(10, 46), "сегодня, 10:46"],
    [at(23, 5, 21), "21.10"],
    [at(8, 0, 30, 11, 2025), "30.12.2025"],
  ])("%s → %s", (iso, text) => {
    expect(whenText(iso, NOW)).toBe(text);
  });
});

test.each([
  [1, "страница"],
  [3, "страницы"],
  [5, "страниц"],
  [11, "страниц"],
  [21, "страница"],
  [22, "страницы"],
])("plural %i → %s", (n, word) => {
  expect(plural(n, ["страница", "страницы", "страниц"])).toBe(word);
});

describe("uploadErrorText", () => {
  // Коды ответов загрузки — раздел 4 спеки #30; тексты — аннотации кадра 07в.
  test.each([
    [{ status: 413, code: "document_too_large" }, "файл больше 10 МБ"],
    [{ status: 415, code: "unsupported_document_type" }, "подходят PDF, JPEG и PNG"],
    [{ status: 422, code: "too_many_pages" }, "в документе больше 6 страниц"],
    [{ status: 422, code: "unreadable_document" }, "файл не открывается"],
    [{ status: 422, code: "invalid_request" }, "файл не отправился, выберите его ещё раз"],
    [{ status: 503, code: "storage_unavailable" }, "хранилище недоступно, попробуйте позже"],
    [{ status: 413 }, "файл больше 10 МБ"],
    ["<html>Bad gateway</html>", "файл не загружен, проверьте связь и попробуйте ещё раз"],
    [new TypeError("Failed to fetch"), "файл не загружен, проверьте связь и попробуйте ещё раз"],
  ])("%j → %s", (error, text) => {
    expect(uploadErrorText(error)).toBe(text);
  });
});

test.each([
  [{ status: 409, code: "document_not_failed" }, "документ уже обрабатывается"],
  [{ status: 500 }, "повтор не запущен, проверьте связь и попробуйте ещё раз"],
])("retryErrorText %j → %s", (error, text) => {
  expect(retryErrorText(error)).toBe(text);
});

describe("selectedDocument", () => {
  const docs = [document({ id: "new" }), document({ id: "old" })];

  test.each([
    [undefined, "new"],
    ["old", "old"],
    ["missing", "new"],
  ])("selection %s shows %s", (id, shown) => {
    expect(selectedDocument(docs, id)?.id).toBe(shown);
  });

  test("no documents — nothing selected", () => {
    expect(selectedDocument([], "old")).toBeUndefined();
  });
});
