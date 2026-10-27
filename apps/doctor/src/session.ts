import {
  fetchClient,
  fetchCurrentUser,
  homePathFor,
  type Role,
  type User,
} from "@compass/api-client";
import type { QueryClient } from "@tanstack/react-query";

export const ME_KEY = ["me"] as const;
const APP_PATH = "/doctor/";

/**
 * Transition to the start page of a role from `homePathFor`. A page of this application
 * opens without a reload; the patient application needs a full page load.
 */
export function homeLocation(role: Role): { href: string; reloadDocument: boolean; replace: true } {
  const href = homePathFor(role);
  return { href, reloadDocument: !href.startsWith(APP_PATH), replace: true };
}

/**
 * Проверка сессии не удалась: нет связи или сервер ответил ошибкой. Это не выход из
 * кабинета: кэш остаётся, корневой маршрут показывает «Не удалось загрузить кабинет».
 */
export class SessionUnavailableError extends Error {
  constructor(cause: unknown) {
    super("Не удалось проверить сессию", { cause });
    this.name = "SessionUnavailableError";
  }
}

export function isSessionUnavailable(error: unknown): error is SessionUnavailableError {
  return error instanceof SessionUnavailableError;
}

/**
 * Текущий пользователь из кэша или с сервера; null — сессии нет. Пока в кэше есть сессия,
 * запрос не уходит: при обрыве сети кабинет живёт на данных из кэша (кадр 12).
 */
export async function currentUser(queryClient: QueryClient): Promise<User | null> {
  let user: User | null;
  try {
    user = await queryClient.ensureQueryData({
      queryKey: ME_KEY,
      queryFn: () => fetchCurrentUser(fetchClient),
    });
  } catch (error) {
    throw new SessionUnavailableError(error);
  }
  if (!user) {
    await queryClient.cancelQueries();
    queryClient.clear();
  }
  return user;
}
