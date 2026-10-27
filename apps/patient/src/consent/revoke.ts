/**
 * Отзыв согласия на обработку данных (спека #112, разделы 3.4 и 3.6): тексты и разбор ошибок.
 * Чистые функции, без React.
 */

/** Пояснение на экране входа: после отзыва и при входе отключённого пациента. */
export const CONSENT_REVOKED_TEXT =
  "Вы отозвали согласие на обработку данных. Вход закрыт. Чтобы вернуться, обратитесь к своему врачу.";

export const CONSENT_REVOKE_UNKNOWN_TEXT =
  "Не удалось узнать результат отзыва согласия. Сессия завершена, локальный доступ закрыт. Уточните состояние согласия у врача.";

/** Причина, с которой экран отзыва открывает вход. */
export type LoginSearch = { reason?: "consent_revoked" | "consent_revoke_unknown" };

/** Что будет после отзыва — простыми словами, по кадру «Пациент · 30». */
export const REVOKE_CONSEQUENCES = [
  {
    id: "data",
    title: "Новые данные",
    text: "Отметки, записи, ответы на опросы и сообщения больше не принимаются.",
  },
  {
    id: "login",
    title: "Вход",
    text: "Вы выйдете на всех устройствах. Войти снова не получится.",
  },
  {
    id: "record",
    title: "Что уже записано",
    text: "Останется у врача в медицинской карте. Медицинские данные не удаляются.",
  },
  {
    id: "doctor",
    title: "Врач",
    text: "Увидит, что согласие отозвано, и дату. Вернуться можно через врача.",
  },
] as const;

/** Ответ входа 403 `consent_revoked`: пароль верный, пациент отозвал согласие. */
export function isConsentRevokedError(error: unknown): boolean {
  if (typeof error !== "object" || error === null) return false;
  const { status, code } = error as { status?: unknown; code?: unknown };
  return status === 403 && code === "consent_revoked";
}

/** Параметры адреса экрана входа: известная причина остаётся, прочее отбрасывается. */
export function parseLoginSearch(search: Record<string, unknown>): LoginSearch {
  return search.reason === "consent_revoked" || search.reason === "consent_revoke_unknown"
    ? { reason: search.reason }
    : {};
}

/**
 * Почему отзыв не прошёл. Без связи ответ мог потеряться уже после отзыва, поэтому
 * о состоянии согласия здесь не говорим.
 */
export function revokeErrorText(error: unknown): string {
  if (error instanceof TypeError) {
    return "Нет связи с сервером. Проверьте интернет и повторите.";
  }
  return "Не удалось узнать результат отзыва согласия. Проверьте связь и попробуйте ещё раз.";
}

/**
 * Предупреждение о неотправленных ответах на устройстве. После отзыва войти нельзя, и врач
 * их не получит, поэтому после отзыва они удаляются. `null` у счётчика — посчитать не удалось.
 */
export function unsentNotice(counts: { drafts: number; outbox: number } | null): string | null {
  if (counts === null) {
    return "Неотправленные ответы на этом устройстве врач не получит. После отзыва они удалятся с устройства.";
  }
  const total = counts.drafts + counts.outbox;
  if (total === 0) return null;
  return `Неотправленных ответов на этом устройстве: ${total}. Врач их не получит. После отзыва они удалятся с устройства.`;
}
