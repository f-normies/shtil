import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test } from "vitest";
import { LogoutDialog } from "./LogoutDialog";

const base = {
  counts: { drafts: 2, unsent: 1 },
  countsLoading: false,
  clearLocal: false,
  pending: false,
  failed: false,
  onClearLocalChange: () => undefined,
  onConfirm: () => undefined,
  onClose: () => undefined,
};

describe("LogoutDialog: модальный <dialog> — Esc, фокус и ловушка штатные (D1-09)", () => {
  test("это <dialog> с заголовком, числами и кнопками", () => {
    const html = renderToStaticMarkup(<LogoutDialog {...base} />);
    expect(html).toContain("<dialog");
    expect(html).toContain("Выйти из кабинета?");
    expect(html).toContain("Черновики на этом устройстве: 2. Неотправленные сообщения: 1.");
    expect(html).toContain("Остаться");
    expect(html).toContain("Выйти</span>");
  });

  test("пока выходим — кнопки неактивны, подпись «Выходим…»", () => {
    const html = renderToStaticMarkup(<LogoutDialog {...base} pending />);
    expect(html).toContain("Выходим…");
    expect(html.match(/disabled=""/g)?.length).toBeGreaterThanOrEqual(2);
  });

  test("ошибка выхода — строкой с role=alert", () => {
    const html = renderToStaticMarkup(<LogoutDialog {...base} failed />);
    expect(html).toContain('role="alert"');
    expect(html).toContain("Не удалось выйти");
  });
});
