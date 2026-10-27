/** Подпись принятого согласия для профиля и экрана отзыва (кадры 29 и 30). Чистые функции. */

const dateFormat = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  timeZone: "Europe/Moscow",
});

/** Человеческое имя редакции: из «2026-10-16-draft» — «от 16.10.2026», иначе как есть. */
export function consentVersionLabel(version: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(version);
  if (!match) return version;
  return `от ${match[3]}.${match[2]}.${match[1]}`;
}

/** Дата по Москве в виде «27.10.2026», без точки в конце. */
export function formatConsentDate(iso: string): string {
  return dateFormat.format(new Date(iso));
}

/** «Версия 1, принята 15.10.2026» — как в чипе макета. */
export function describeAcceptedConsent(consent: { version: string; given_at: string }): string {
  return `Версия ${consentVersionLabel(consent.version)}, принята ${formatConsentDate(consent.given_at)}`;
}
