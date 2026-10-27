import { detailOf } from "../../card/errors";

/**
 * Ошибки решения по предложению терапии — по-русски, после «Ошибка:» у `Alert`.
 * Тексты API врачу не показываем.
 */
export function therapyError(error: unknown): string {
  const detail = detailOf(error);
  if (detail === "Proposal is already decided") {
    return "предложение уже рассмотрено. Нажмите «Подобрать терапию», чтобы обновить.";
  }
  if (typeof detail === "string" && detail.startsWith("Off-label")) {
    return "схема вне инструкции: напишите обоснование не короче 10 символов.";
  }
  if (Array.isArray(detail)) {
    return "проверьте схему: доза — положительное число, кратность и длительность — целые числа.";
  }
  if (typeof detail === "string" && /[а-я]/i.test(detail)) {
    return detail;
  }
  return "решение не сохранено. Проверьте связь и попробуйте ещё раз.";
}
