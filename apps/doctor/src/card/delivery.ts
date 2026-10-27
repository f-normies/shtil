/**
 * Повтор отметки о доставке сигнала (спека #12, #13). Без повтора разовый сбой оставил бы
 * сигнал недоставленным до следующего подключения, хотя врач его уже видит.
 */

/** Ответ openapi-fetch. На 4xx и 5xx он не бросает исключение, а при пустом теле не заполняет `error`. */
type Sent = { response: { ok: boolean } };

/**
 * Отправить запрос и при неудаче повторить один раз. Неудача — ответ не 2xx или сбой сети.
 * Возвращает, прошёл ли запрос; исключений не бросает.
 */
export async function retryOnce(send: () => Promise<Sent>): Promise<boolean> {
  for (let attempt = 0; attempt < 2; attempt++) {
    const ok = await send().then(
      ({ response }) => response.ok,
      () => false,
    );
    if (ok) {
      return true;
    }
  }
  return false;
}
