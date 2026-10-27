import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test } from "vitest";
import { ActionSteps } from "./ActionSteps";

describe("ActionSteps: «Что сделать» — нумерованный список, не абзац (D1-04)", () => {
  test("каждый пункт — своя строка с номером", () => {
    const html = renderToStaticMarkup(
      <ActionSteps actions={["Связаться с пациентом сегодня", "Оценить суицидальный риск"]} />,
    );
    expect(html).toContain("<ol");
    expect(html.match(/<li/g)).toHaveLength(2);
    expect(html).toContain(">1<");
    expect(html).toContain(">2<");
    expect(html).not.toContain("сегодня Оценить");
  });
});
