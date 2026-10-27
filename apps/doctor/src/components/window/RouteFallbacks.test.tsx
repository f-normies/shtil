import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test } from "vitest";
import { LoadFailed, NotFound } from "./RouteFallbacks";

describe("запасные экраны маршрутов — по-русски, с выходом (D1-03, D1-08)", () => {
  test("ошибка загрузки: текст и кнопка «Обновить»", () => {
    const html = renderToStaticMarkup(<LoadFailed />);
    expect(html).toContain("Не удалось загрузить кабинет. Проверьте связь и обновите страницу");
    expect(html).toContain("Обновить");
    expect(html).toContain('role="alert"');
  });

  test("нет страницы: текст и ссылка к пациентам", () => {
    const html = renderToStaticMarkup(<NotFound />);
    expect(html).toContain("Такой страницы нет");
    expect(html).toContain('href="/doctor/"');
    expect(html).toContain("К пациентам");
  });
});
