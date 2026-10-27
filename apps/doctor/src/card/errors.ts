/**
 * Поле `detail` ответа об ошибке. Схема OpenAPI описывает не все ответы (404, 409),
 * поэтому значение — `unknown`: строка у HTTPException, список у ошибки проверки 422.
 */
export function detailOf(error: unknown): unknown {
  return typeof error === "object" && error !== null && "detail" in error ? error.detail : null;
}
