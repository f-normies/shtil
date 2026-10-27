/** Русские подписи кабинета пациента: склонения и имя в приветствии. Чистые функции. */

const MONTHS_PREPOSITIONAL = [
  "январе",
  "феврале",
  "марте",
  "апреле",
  "мае",
  "июне",
  "июле",
  "августе",
  "сентябре",
  "октябре",
  "ноябре",
  "декабре",
];

/** «запись», «записи», «записей» — когда число стоит отдельно от слова. */
export function entriesNoun(count: number): string {
  const lastTwo = count % 100;
  const last = count % 10;
  if (lastTwo >= 11 && lastTwo <= 14) return "записей";
  if (last === 1) return "запись";
  if (last >= 2 && last <= 4) return "записи";
  return "записей";
}

/** «1 запись», «2 записи», «5 записей» (P2-04). */
export function entriesCountLabel(count: number): string {
  return `${count} ${entriesNoun(count)}`;
}

/** «в октябре» по дате `YYYY-MM-DD` (P2-04). */
export function monthInPrepositional(date: string): string {
  const month = Number(date.slice(5, 7));
  return `в ${MONTHS_PREPOSITIONAL[month - 1] ?? date.slice(0, 7)}`;
}

/** Заглавная только у первой буквы: «Вторник, 27 октября» (P2-11). */
export function capitalizeFirst(text: string): string {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : "";
}

/**
 * Имя для приветствия (M-8): обращение из онбординга, если оно задано и отличается от ФИО;
 * иначе имя — второе слово ФИО «Фамилия Имя Отчество».
 */
export function greetingName(
  displayName: string | undefined,
  fullName: string | undefined,
): string {
  const display = displayName?.trim() ?? "";
  const full = fullName?.trim() ?? "";
  if (display && display !== full) return display;
  const words = full.split(/\s+/).filter(Boolean);
  return words[1] ?? words[0] ?? "";
}
