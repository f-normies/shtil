import { detailOf } from "./errors";

const NOT_FOUND = "пациент не найден.";
const UNAVAILABLE = "не удалось загрузить карточку. Проверьте связь и обновите страницу.";

/**
 * Текст ошибки карточки (кадр 05). Чужого и несуществующего пациента API не различает —
 * оба 404; адрес не в формате UUID даёт 422, для врача это та же «нет такого пациента».
 */
export function patientLoadMessage(error: unknown): string {
  const status =
    typeof error === "object" && error !== null && "status" in error ? error.status : undefined;
  if (status === 404 || status === 422 || detailOf(error) === "Patient not found") {
    return NOT_FOUND;
  }
  return UNAVAILABLE;
}
