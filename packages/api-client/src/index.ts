/**
 * Клиент API для фронтендов. Типы генерируются из OpenAPI бэкенда (`make gen-client`),
 * файл `schema.d.ts` руками не править.
 */
import createFetchClient from "openapi-fetch";
import createQueryClient from "openapi-react-query";
import type { components, paths } from "./schema";

/** Тот же origin, что у фронтенда: cookie сессии уходит автоматически. */
export const fetchClient = createFetchClient<paths>({ baseUrl: "", credentials: "same-origin" });

/** Хуки TanStack Query: `$api.useQuery("get", "/api/v1/auth/me")`. */
export const $api = createQueryClient(fetchClient);

export type Schemas = components["schemas"];
export type User = Schemas["UserOut"];
export type Role = User["role"];

/** Ошибка API в формате RFC 9457. */
export type Problem = {
  type: string;
  title: string;
  status: number;
  detail?: string;
};

export {
  expireSession,
  fetchCurrentUser,
  getSessionGeneration,
  homePathFor,
  installSessionExpiryHandler,
  isSessionExpiryPending,
  shouldRetryApiQuery,
} from "./auth";
export * from "./messaging";
export {
  loginErrorMessage,
  loginFieldErrors,
  retryAfterSeconds,
  retryAfterText,
} from "./problem";
