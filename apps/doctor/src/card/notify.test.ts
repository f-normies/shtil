import { afterEach, describe, expect, test, vi } from "vitest";
import { canAskNotifications, notifyUrgent } from "./notify";

type Shown = { title: string; options?: NotificationOptions };

function stubNotification(permission: NotificationPermission): Shown[] {
  const shown: Shown[] = [];
  class FakeNotification {
    static permission = permission;
    constructor(title: string, options?: NotificationOptions) {
      shown.push({ title, options });
    }
  }
  vi.stubGlobal("Notification", FakeNotification);
  return shown;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("уведомление браузера (спека #56, раздел 6)", () => {
  test("без пациента и сигнала: «Штиль» и «Срочный сигнал ждёт решения»", () => {
    const shown = stubNotification("granted");

    notifyUrgent();

    expect(shown).toEqual([
      { title: "Штиль", options: { body: "Срочный сигнал ждёт решения", tag: "shtil-urgent" } },
    ]);
  });

  test("без разрешения ничего не показывает", () => {
    const shown = stubNotification("denied");

    notifyUrgent();

    expect(shown).toEqual([]);
  });

  test("ссылка «Включить уведомления» — пока врач не ответил на запрос", () => {
    stubNotification("default");
    expect(canAskNotifications()).toBe(true);
    stubNotification("granted");
    expect(canAskNotifications()).toBe(false);
  });
});
