/**
 * Уведомление браузера о срочном сигнале (спека #56, раздел 6): единственный повтор, когда
 * вкладка в фоне. Внешний канал: без пациента и сигнала, только «Штиль» и общая фраза.
 */

const TITLE = "Штиль";
const BODY = "Срочный сигнал ждёт решения";
// Новое уведомление заменяет прежнее: на экране одно, сколько бы шагов ни прошло.
const TAG = "shtil-urgent";

function supported(): boolean {
  return typeof globalThis.Notification !== "undefined";
}

/** Врач ещё не ответил на запрос разрешения: показываем «Включить уведомления». */
export function canAskNotifications(): boolean {
  return supported() && Notification.permission === "default";
}

/** Спросить разрешение. Ответ браузер запоминает сам. */
export async function askNotifications(): Promise<void> {
  if (canAskNotifications()) {
    await Notification.requestPermission();
  }
}

/** Показать уведомление, если врач разрешил. */
export function notifyUrgent(): void {
  if (supported() && Notification.permission === "granted") {
    new Notification(TITLE, { body: BODY, tag: TAG });
  }
}
