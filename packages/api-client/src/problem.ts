/** Разбор ошибок API в формате RFC 9457, общий для обоих кабинетов. */

const MINUTE = 60;
const HOUR = 60 * MINUTE;

/**
 * Когда можно повторить запрос после 429 (спека #23): «через несколько секунд»,
 * «через N мин.» или «через N ч.», с округлением вверх. Для других ошибок — `null`.
 * Секунды берутся из тела: клиент не читает заголовки ошибки.
 */
export function retryAfterText(error: unknown): string | null {
  if (typeof error !== "object" || error === null) {
    return null;
  }
  const { status, retry_after: seconds } = error as { status?: unknown; retry_after?: unknown };
  if (status !== 429 || typeof seconds !== "number") {
    return null;
  }
  if (seconds < MINUTE) {
    return "через несколько секунд";
  }
  if (seconds <= HOUR) {
    return `через ${Math.ceil(seconds / MINUTE)} мин.`;
  }
  return `через ${Math.ceil(seconds / HOUR)} ч.`;
}

/** Подсказка под полем формы входа: где показать и что сказать. Текст без вины, с точкой. */
export function loginErrorMessage(error: unknown): { field: "email" | "password"; text: string } {
  const wait = retryAfterText(error);
  if (wait !== null) {
    const text = `Слишком много попыток входа. Повторите ${wait}`;
    return { field: "password", text: text.endsWith(".") ? text : `${text}.` };
  }
  const status =
    typeof error === "object" && error !== null && "status" in error ? error.status : undefined;
  switch (status) {
    case 401:
      return {
        field: "password",
        text: "Почта или пароль не подошли. Проверьте и попробуйте ещё раз.",
      };
    case 422:
      return { field: "email", text: "Введите адрес почты, например name@example.ru." };
    case 403:
      return { field: "password", text: "Вход закрыт." };
    default:
      // Сеть или 5xx: поля сохраняются, после восстановления связи «Войти» работает (кадр 01).
      return { field: "password", text: "Не удалось войти. Проверьте связь и повторите." };
  }
}

/** Сколько секунд ждать после 429; `null` — ошибка не про лимит. */
export function retryAfterSeconds(error: unknown): number | null {
  if (typeof error !== "object" || error === null) return null;
  const { status, retry_after: seconds } = error as { status?: unknown; retry_after?: unknown };
  return status === 429 && typeof seconds === "number" && seconds > 0 ? seconds : null;
}

/**
 * Проверка полей до запроса (кадр 01): пустое поле — подсказка под ним, запрос не уходит.
 * Формат почты не проверяем: за него отвечает сервер (422).
 */
export function loginFieldErrors(fields: { email: string; password: string }): {
  email?: string;
  password?: string;
} {
  return {
    ...(fields.email.trim() ? {} : { email: "Введите почту." }),
    ...(fields.password ? {} : { password: "Введите пароль." }),
  };
}
