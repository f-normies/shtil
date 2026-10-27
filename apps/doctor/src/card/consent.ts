/**
 * Пометка «Согласие отозвано» в списке пациентов и на плашке подробностей и карточки
 * (спека #112, раздел 3.7; кадры «Врач v4 · 02в» и «05г»).
 * Дата — по часовому поясу браузера, как остальные даты кабинета.
 */

const DAY = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
});
const DAY_MONTH = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit" });

/** Что отзыв значит для данных; показывается на плашке рядом с датой. */
export const CONSENT_REVOKED_EFFECT =
  "Новые данные от пациента не поступают, вход в кабинет закрыт. Собранные данные остаются в карте.";

/** «Согласие отозвано 18.10.2026»; `null` — согласие действует. */
export function consentRevokedLabel(revokedAt: string | null | undefined): string | null {
  if (!revokedAt) return null;
  return `Согласие отозвано ${DAY.format(new Date(revokedAt))}`;
}

/** Вторая строка в списке пациентов: «Согласие отозвано 18.10»; `null` — согласие действует. */
export function consentRevokedShort(revokedAt: string | null | undefined): string | null {
  if (!revokedAt) return null;
  return `Согласие отозвано ${DAY_MONTH.format(new Date(revokedAt))}`;
}
