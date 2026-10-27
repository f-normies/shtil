import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test } from "vitest";
import { ListSkeleton } from "./ListSkeleton";

describe("ListSkeleton: загрузка списка — скелетон 8 строк (кадр 02, D1-11)", () => {
  test("восемь декоративных строк и текст для озвучки", () => {
    const html = renderToStaticMarkup(<ListSkeleton label="Загружаем пациентов…" />);
    expect(html.match(/data-part="skeleton-row"/g)).toHaveLength(8);
    expect(html).toContain('aria-hidden="true"');
    expect(html).toContain("Загружаем пациентов…");
  });
});
