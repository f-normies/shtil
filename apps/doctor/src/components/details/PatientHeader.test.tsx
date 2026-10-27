import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test } from "vitest";
import { PatientHeader } from "./PatientHeader";

const LONG = "Константинопольская Александрина Владиславовна";

describe("PatientHeader: ФИО своей строкой, сведения и кнопки — второй (кадры 03, 05)", () => {
  test("имя стоит отдельно от строки со сведениями и действиями", () => {
    const html = renderToStaticMarkup(
      <PatientHeader
        name={LONG}
        about="34 года, F41.1, наблюдение с 22.09"
        actions={<button type="button">Написать</button>}
      />,
    );
    const name = html.indexOf(LONG);
    const row = html.indexOf('data-part="about-row"');
    const about = html.indexOf("34 года");
    const action = html.indexOf("Написать");
    expect(name).toBeGreaterThan(-1);
    // Заголовок закрыт до начала строки сведений: длинное ФИО не теснит кнопки.
    expect(html.indexOf("</h2>", name)).toBeLessThan(row);
    expect(about).toBeGreaterThan(row);
    expect(action).toBeGreaterThan(about);
  });

  test("заголовок первого уровня в карточке", () => {
    const html = renderToStaticMarkup(<PatientHeader name="Тестова Анна" about="" large />);
    expect(html).toContain("<h1");
    expect(html).not.toContain("<h2");
  });
});
