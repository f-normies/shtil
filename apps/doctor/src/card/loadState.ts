/**
 * Ошибку загрузки показываем только когда показать нечего (кадр 12): при обрыве сети
 * данные из кэша остаются на экране, о задержке говорит строка «Нет связи с сервером».
 */
export function showsFailure(query: { isError: boolean; data: unknown }): boolean {
  return query.isError && query.data === undefined;
}
